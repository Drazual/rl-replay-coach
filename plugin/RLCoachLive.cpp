// RL Coach Live: a BakkesMod plugin that streams your car and controller inputs
// to the RL Coach app so it can grade mechanics (half flips, speed flips,
// wavedashes, fast aerials, landings) and shows the app's tips on screen.
//
// BakkesMod only runs when Rocket League is started WITHOUT Easy Anti-Cheat,
// which the game allows for training, free play, offline/LAN matches and
// replays. On top of that this plugin sends nothing during online games.
//
// Everything is local: UDP to 127.0.0.1 only.

#define WIN32_LEAN_AND_MEAN
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
#include <shellapi.h>

#include <chrono>
#include <cmath>
#include <cstdio>
#include <fstream>
#include <mutex>
#include <string>

#include "bakkesmod/plugin/bakkesmodplugin.h"

#pragma comment(lib, "ws2_32.lib")
#pragma comment(lib, "shell32.lib")

namespace {
constexpr unsigned short FRAME_PORT = 49210;  // plugin -> app
constexpr unsigned short TIP_PORT = 49211;    // app -> plugin
constexpr unsigned short APP_PORT = 49209;    // the app's local web server, used to see if it is running
constexpr double ROT_TO_RAD = 3.14159265358979323846 / 32768.0;

std::string JsonEscape(const std::string& s) {
    std::string out;
    for (char c : s) {
        if (c == '"' || c == '\\') { out += '\\'; out += c; }
        else if (static_cast<unsigned char>(c) < 0x20) out += ' ';
        else out += c;
    }
    return out;
}

// Pulls "key": "value" out of a small JSON message from the app.
std::string JsonString(const std::string& json, const std::string& key) {
    auto k = json.find("\"" + key + "\"");
    if (k == std::string::npos) return "";
    auto q = json.find('"', json.find(':', k) + 1);
    if (q == std::string::npos) return "";
    std::string out;
    for (size_t i = q + 1; i < json.size(); ++i) {
        if (json[i] == '\\' && i + 1 < json.size()) { out += json[++i]; continue; }
        if (json[i] == '"') break;
        out += json[i];
    }
    return out;
}

double JsonNumber(const std::string& json, const std::string& key, double fallback) {
    auto k = json.find("\"" + key + "\"");
    if (k == std::string::npos) return fallback;
    try { return std::stod(json.substr(json.find(':', k) + 1)); } catch (...) { return fallback; }
}
}  // namespace

class RLCoachLive : public BakkesMod::Plugin::BakkesModPlugin {
public:
    void onLoad() override;
    void onUnload() override;

private:
    void OnVehicleInput(CarWrapper car);
    void Draw(CanvasWrapper canvas);
    void PollTips();
    std::string Mode();
    void Send(const std::string& msg);
    void LaunchAppIfNeeded();

    SOCKET out_ = INVALID_SOCKET;
    SOCKET in_ = INVALID_SOCKET;
    sockaddr_in app_{};
    std::chrono::steady_clock::time_point start_ = std::chrono::steady_clock::now();
    std::chrono::steady_clock::time_point last_send_{};
    std::string last_mode_;

    std::mutex tip_mutex_;
    std::string tip_;
    std::chrono::steady_clock::time_point tip_until_{};
};

BAKKESMOD_PLUGIN(RLCoachLive, "RL Coach Live", "0.3.0", PLUGINTYPE_FREEPLAY | PLUGINTYPE_CUSTOM_TRAINING)

void RLCoachLive::onLoad() {
    WSADATA wsa;
    WSAStartup(MAKEWORD(2, 2), &wsa);
    out_ = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    app_.sin_family = AF_INET;
    app_.sin_port = htons(FRAME_PORT);
    inet_pton(AF_INET, "127.0.0.1", &app_.sin_addr);

    in_ = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    sockaddr_in me{};
    me.sin_family = AF_INET;
    me.sin_port = htons(TIP_PORT);
    inet_pton(AF_INET, "127.0.0.1", &me.sin_addr);
    bind(in_, reinterpret_cast<sockaddr*>(&me), sizeof(me));
    u_long nonblocking = 1;
    ioctlsocket(in_, FIONBIO, &nonblocking);

    cvarManager->registerCvar("rlcoach_enabled", "1", "Stream car data to RL Coach", true, true, 0, true, 1);
    cvarManager->registerCvar("rlcoach_overlay", "1", "Show RL Coach tips on screen", true, true, 0, true, 1);
    cvarManager->registerCvar("rlcoach_autolaunch", "1", "Open RL Coach when Rocket League starts", true, true, 0, true, 1);
    cvarManager->registerCvar("rlcoach_rate", "120", "Frames per second sent to RL Coach", true, true, 10, true, 240);
    cvarManager->registerCvar("rlcoach_app_path", "", "Full path to RLCoach.exe (blank = use the path the app saved)");

    gameWrapper->HookEventWithCaller<CarWrapper>(
        "Function TAGame.Car_TA.SetVehicleInput",
        [this](CarWrapper car, void*, std::string) { OnVehicleInput(car); });
    gameWrapper->RegisterDrawable([this](CanvasWrapper canvas) { Draw(canvas); });

    gameWrapper->SetTimeout([this](GameWrapper*) { LaunchAppIfNeeded(); }, 2.0f);
    Send("{\"type\":\"hello\",\"mode\":\"" + Mode() + "\"}");
}

void RLCoachLive::onUnload() {
    if (out_ != INVALID_SOCKET) closesocket(out_);
    if (in_ != INVALID_SOCKET) closesocket(in_);
    WSACleanup();
}

std::string RLCoachLive::Mode() {
    if (gameWrapper->IsInOnlineGame()) return "online";
    if (gameWrapper->IsInFreeplay()) return "freeplay";
    if (gameWrapper->IsInCustomTraining()) return "training";
    if (gameWrapper->IsInReplay()) return "replay";
    if (gameWrapper->IsInGame()) return "offline";
    return "menu";
}

void RLCoachLive::Send(const std::string& msg) {
    if (out_ == INVALID_SOCKET) return;
    sendto(out_, msg.c_str(), static_cast<int>(msg.size()), 0, reinterpret_cast<sockaddr*>(&app_), sizeof(app_));
}

void RLCoachLive::OnVehicleInput(CarWrapper car) {
    if (!cvarManager->getCvar("rlcoach_enabled").getBoolValue()) return;
    std::string mode = Mode();
    if (mode != last_mode_) {
        last_mode_ = mode;
        Send("{\"type\":\"mode\",\"mode\":\"" + mode + "\"}");
    }
    if (mode == "online" || mode == "menu") return;  // never during online play

    CarWrapper local = gameWrapper->GetLocalCar();
    if (local.IsNull() || car.IsNull() || car.memory_address != local.memory_address) return;

    auto now = std::chrono::steady_clock::now();
    double rate = cvarManager->getCvar("rlcoach_rate").getFloatValue();
    if (rate > 0 && std::chrono::duration<double>(now - last_send_).count() < 1.0 / rate) return;
    last_send_ = now;

    Vector loc = car.GetLocation(), vel = car.GetVelocity(), ang = car.GetAngularVelocity();
    Rotator rot = car.GetRotation();
    ControllerInput in = car.GetInput();
    float boost = 0;
    BoostWrapper bc = car.GetBoostComponent();
    if (!bc.IsNull()) boost = bc.GetCurrentBoostAmount() * 100.0f;
    double t = std::chrono::duration<double>(now - start_).count();

    std::string ball = "null";
    ServerWrapper server = gameWrapper->GetCurrentGameState();
    if (!server.IsNull()) {
        BallWrapper b = server.GetBall();
        if (!b.IsNull()) {
            Vector bl = b.GetLocation(), bv = b.GetVelocity();
            char buf[160];
            snprintf(buf, sizeof(buf), "{\"loc\":[%.1f,%.1f,%.1f],\"vel\":[%.1f,%.1f,%.1f]}", bl.X, bl.Y, bl.Z, bv.X, bv.Y, bv.Z);
            ball = buf;
        }
    }

    char msg[900];
    snprintf(msg, sizeof(msg),
             "{\"type\":\"frame\",\"mode\":\"%s\",\"t\":%.4f,"
             "\"car\":{\"loc\":[%.1f,%.1f,%.1f],\"vel\":[%.1f,%.1f,%.1f],\"angvel\":[%.3f,%.3f,%.3f],"
             "\"rot\":[%.5f,%.5f,%.5f],\"boost\":%.0f,\"ground\":%s,\"wall\":%s,\"jumped\":%s,\"djumped\":%s,\"dodging\":%s},"
             "\"in\":{\"thr\":%.2f,\"steer\":%.2f,\"pitch\":%.2f,\"yaw\":%.2f,\"roll\":%.2f,\"jump\":%d,\"boost\":%d,\"hb\":%d},"
             "\"ball\":%s}",
             mode.c_str(), t, loc.X, loc.Y, loc.Z, vel.X, vel.Y, vel.Z, ang.X, ang.Y, ang.Z,
             rot.Pitch * ROT_TO_RAD, rot.Yaw * ROT_TO_RAD, rot.Roll * ROT_TO_RAD, boost,
             car.IsOnGround() ? "true" : "false", car.IsOnWall() ? "true" : "false",
             car.GetbJumped() ? "true" : "false", car.GetbDoubleJumped() ? "true" : "false",
             car.IsDodging() ? "true" : "false",
             in.Throttle, in.Steer, in.Pitch, in.Yaw, in.Roll, in.Jump ? 1 : 0, in.HoldingBoost ? 1 : 0,
             in.Handbrake ? 1 : 0, ball.c_str());
    Send(msg);
}

void RLCoachLive::PollTips() {
    char buf[2048];
    for (;;) {
        int n = recv(in_, buf, sizeof(buf) - 1, 0);
        if (n <= 0) break;
        buf[n] = 0;
        std::string msg(buf);
        if (JsonString(msg, "type") != "tip") continue;
        std::lock_guard<std::mutex> lock(tip_mutex_);
        tip_ = JsonString(msg, "text");
        double secs = JsonNumber(msg, "seconds", 6.0);
        tip_until_ = std::chrono::steady_clock::now() + std::chrono::milliseconds(static_cast<int>(secs * 1000));
    }
}

void RLCoachLive::Draw(CanvasWrapper canvas) {
    PollTips();
    if (!cvarManager->getCvar("rlcoach_overlay").getBoolValue()) return;
    std::string mode = Mode();
    if (mode == "online" || mode == "menu") return;
    std::string tip;
    {
        std::lock_guard<std::mutex> lock(tip_mutex_);
        if (std::chrono::steady_clock::now() < tip_until_) tip = tip_;
    }
    if (tip.empty()) return;

    Vector2 screen = canvas.GetSize();
    const float scale = 1.6f;
    Vector2F size = canvas.GetStringSize(tip, scale, scale);
    float w = size.X + 28, h = size.Y + 18;
    float x = (screen.X - w) / 2.0f, y = screen.Y * 0.12f;
    canvas.SetColor(10, 14, 20, 190);
    canvas.SetPosition(Vector2F{x, y});
    canvas.FillBox(Vector2F{w, h});
    canvas.SetColor(79, 157, 255, 255);
    canvas.SetPosition(Vector2F{x, y});
    canvas.FillBox(Vector2F{4, h});
    canvas.SetColor(235, 240, 245, 255);
    canvas.SetPosition(Vector2F{x + 14, y + 9});
    canvas.DrawString(tip, scale, scale, true);
}

void RLCoachLive::LaunchAppIfNeeded() {
    if (!cvarManager->getCvar("rlcoach_autolaunch").getBoolValue()) return;

    // Already running? The app keeps its window's web server on APP_PORT.
    SOCKET probe = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_port = htons(APP_PORT);
    inet_pton(AF_INET, "127.0.0.1", &addr.sin_addr);
    bool running = connect(probe, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) == 0;
    closesocket(probe);
    if (running) return;

    // Where is the app? The cvar wins; otherwise the path the app saved when it last ran.
    std::string cmd = cvarManager->getCvar("rlcoach_app_path").getStringValue();
    if (cmd.empty()) {
        char appdata[MAX_PATH];
        DWORD n = GetEnvironmentVariableA("APPDATA", appdata, MAX_PATH);
        if (n > 0 && n < MAX_PATH) {
            std::ifstream f(std::string(appdata) + "\\rlcoach\\launch.txt");
            std::getline(f, cmd);
        }
    }
    if (cmd.empty()) {
        cvarManager->log("RL Coach: open the RL Coach app once so this plugin knows where it is.");
        return;
    }
    cmd += " --live";
    STARTUPINFOA si{};
    si.cb = sizeof(si);
    PROCESS_INFORMATION pi{};
    if (CreateProcessA(nullptr, cmd.data(), nullptr, nullptr, FALSE, DETACHED_PROCESS, nullptr, nullptr, &si, &pi)) {
        CloseHandle(pi.hThread);
        CloseHandle(pi.hProcess);
    } else {
        cvarManager->log("RL Coach: could not start the app with: " + cmd);
    }
}

// dlss5_host.cpp
//
// Minimal D3D11 host for the Phase 4 proof of concept.
//
// It does NOT call NGX itself. It presents one image (Arnold beauty) with a real depth buffer
// (from the Arnold Z AOV) bound during the draw, so the user's unmodified ReShade +
// dlss5-feed + renodx-dlss5 stack (loaded from runtime\ as dxgi.dll) sees the same thing it
// sees in a game: back buffer = colour, ReShade Generic Depth = depth. After a warm-up the
// post-ReShade back buffer (which then holds the neural rendering output) is read back.
//
// Usage:
//   dlss5_host.exe --color beauty.pfm --depth z.pfm --out result.ppm [options]
//   dlss5_host.exe --list frames.txt [options]
//       frames.txt: one frame per line, "color.pfm<TAB>depth.pfm<TAB>out.ppm", in playback order.
//       The whole list runs in one session, so the neural pass keeps its temporal history.
// Options:
//   --warmup N     presents on the first frame before capturing (default 80; the feed holds
//                  feature creation for 60 frames)
//   --warmup-sec s minimum seconds before the first capture (default 1)
//   --fx-capture 0 capture post-Present instead of at reshade_finish_effects (the default capture
//                  point is before ReShade's overlay/banner, so it never appears in the output)
//   --per-frame N  presents per later frame before capturing (default 3)
//   --near n       camera near clip used for reversed-Z (default 0.1)
//   --exposure s   exposure in stops applied before sRGB encoding (default 0)
//   --hdr 1        16-bit float scRGB back buffer (linear Rec.709, 1.0 = 80 nits); colour input is
//                  scRGB-scaled linear, output is written as a float PFM (use a .pfm --out)
//   --display-referred 1  colour is already view-transformed sRGB (e.g. Arnold's ACES output
//                  transform PNG): quantise only, no sRGB encode, exposure ignored
//
// Input colour is linear scene-referred RGB (PFM, 3 channels); it is encoded to sRGB 8-bit
// because the Fallout 4 path this stack was proven on is SDR R8G8B8A8_UNORM.
// Input depth is camera-space Z (PFM, 1 channel); it is written as reversed-Z (near/Z),
// matching the "depth reversed=1" the feed detected in the game.
//
// Motion vectors (optional 4th list column, PFM from Arnold's raw 'motionvector' AOV as exported
// by dlss5_enhance): this exe registers itself as a ReShade add-on and, right before ReShade
// renders its effects, uploads them into DLSS5_Feed.fx's shared 'texMotionVectors' texture (the
// feed's provider 0), converted to the provider convention: delta UV with prev_uv = uv + mv.
// Arnold -> UV mapping, verified by backward-warp tests (tools/test_mv.py): the AOV covers half a
// frame and has y up, so  mv_uv = (-2*dx / W, +2*dy / H).

#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <d3d11.h>
#include <dxgi.h>
#include <d3dcompiler.h>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

#include <reshade.hpp>

#pragma comment(lib, "d3d11.lib")
#pragma comment(lib, "dxgi.lib")
#pragma comment(lib, "d3dcompiler.lib")
#pragma comment(lib, "user32.lib")

template <class T> static void SafeRelease(T*& p) { if (p) { p->Release(); p = nullptr; } }

// ---------------------------------------------------------------------------------------------
// ReShade add-on part: this exe is registered as an add-on so it can write effect textures.

extern "C" __declspec(dllexport) const char* NAME = "DLSS 5 Arnold host";
extern "C" __declspec(dllexport) const char* DESCRIPTION =
    "Feeds Arnold motion vectors into DLSS5_Feed.fx texMotionVectors (dlss5_arnold PoC).";

static reshade::api::effect_runtime* g_runtime = nullptr;
static const uint16_t* g_mvHalf = nullptr;  // RG16F, W*H*2 halves; null = nothing to upload
static uint32_t g_mvW = 0, g_mvH = 0;
static int g_mvUploads = 0;

// Capture point: ReShade's reshade_finish_effects fires after all effects (the DLSS 5 feed + NR
// run inside them) and BEFORE ReShade draws its own overlay/banner into the back buffer. Copying
// the back buffer here gives the clean DLSS 5 result; the post-Present copy is only a fallback.
static ID3D11DeviceContext* g_ctx = nullptr;
static ID3D11Texture2D* g_back = nullptr;
static ID3D11Texture2D* g_fxStaging = nullptr;
static bool g_fxCopied = false;

static void OnFinishEffects(reshade::api::effect_runtime*, reshade::api::command_list*,
                            reshade::api::resource_view, reshade::api::resource_view)
{
    if (g_ctx && g_back && g_fxStaging) {
        g_ctx->CopyResource(g_fxStaging, g_back);
        g_fxCopied = true;
    }
}

static void OnInitRuntime(reshade::api::effect_runtime* rt) { g_runtime = rt; }
static void OnDestroyRuntime(reshade::api::effect_runtime* rt) { if (g_runtime == rt) g_runtime = nullptr; }
static void OnBeginEffects(reshade::api::effect_runtime* rt, reshade::api::command_list*,
                           reshade::api::resource_view, reshade::api::resource_view)
{
    if (!g_mvHalf) return;
    const auto var = rt->find_texture_variable("DLSS5_Feed.fx", "texMotionVectors");
    if (var.handle == 0) return;  // effects not compiled yet, or the feed uses another provider
    rt->update_texture(var, g_mvW, g_mvH, g_mvHalf);
    ++g_mvUploads;
}

static uint16_t FloatToHalf(float f)
{
    uint32_t x; memcpy(&x, &f, 4);
    const uint32_t sign = (x >> 16) & 0x8000;
    int32_t exp = (int32_t)((x >> 23) & 0xFF) - 127 + 15;
    uint32_t mant = x & 0x7FFFFF;
    if (exp <= 0) {                       // subnormal / zero
        if (exp < -10) return (uint16_t)sign;
        mant |= 0x800000;
        return (uint16_t)(sign | (mant >> (14 - exp)));
    }
    if (exp >= 31) return (uint16_t)(sign | 0x7C00);  // overflow -> inf
    return (uint16_t)(sign | (exp << 10) | (mant >> 13));
}

#define CHECK_HR(expr) do { HRESULT hr_ = (expr); if (FAILED(hr_)) { \
    fprintf(stderr, "[host] FAILED 0x%08lX: %s (line %d)\n", (unsigned long)hr_, #expr, __LINE__); \
    return 1; } } while (0)

// ---------------------------------------------------------------------------------------------
// Image I/O: PFM in (Arnold's oiiotool converts EXR <-> PFM), binary PPM out.

struct Image { int w = 0, h = 0, c = 0; std::vector<float> px; };  // rows top-down

static bool ReadPFM(const char* path, Image& img)
{
    FILE* f = fopen(path, "rb");
    if (!f) { fprintf(stderr, "[host] cannot open %s\n", path); return false; }
    char tag[3] = {};
    int w = 0, h = 0; float scale = 0.f;
    if (fscanf(f, "%2s %d %d %f", tag, &w, &h, &scale) != 4) { fclose(f); return false; }
    fgetc(f);  // the single whitespace byte before the raster
    const int c = strcmp(tag, "PF") == 0 ? 3 : strcmp(tag, "Pf") == 0 ? 1 : 0;
    if (!c || w <= 0 || h <= 0) { fclose(f); fprintf(stderr, "[host] %s is not a PFM\n", path); return false; }

    std::vector<float> raw((size_t)w * h * c);
    const size_t got = fread(raw.data(), sizeof(float), raw.size(), f);
    fclose(f);
    if (got != raw.size()) { fprintf(stderr, "[host] %s truncated\n", path); return false; }

    if (scale > 0.f) {  // positive scale = big-endian
        for (float& v : raw) {
            uint32_t u; memcpy(&u, &v, 4);
            u = (u >> 24) | ((u >> 8) & 0xFF00) | ((u << 8) & 0xFF0000) | (u << 24);
            memcpy(&v, &u, 4);
        }
    }
    // PFM stores rows bottom-to-top.
    img.w = w; img.h = h; img.c = c; img.px.resize(raw.size());
    const size_t row = (size_t)w * c;
    for (int y = 0; y < h; ++y)
        memcpy(&img.px[(size_t)y * row], &raw[(size_t)(h - 1 - y) * row], row * sizeof(float));
    return true;
}

static bool WritePPM(const char* path, const uint8_t* rgba, int w, int h, int pitch)
{
    FILE* f = fopen(path, "wb");
    if (!f) return false;
    fprintf(f, "P6\n%d %d\n255\n", w, h);
    std::vector<uint8_t> row((size_t)w * 3);
    for (int y = 0; y < h; ++y) {
        const uint8_t* s = rgba + (size_t)y * pitch;
        for (int x = 0; x < w; ++x) { row[x*3] = s[x*4]; row[x*3+1] = s[x*4+1]; row[x*3+2] = s[x*4+2]; }
        fwrite(row.data(), 1, row.size(), f);
    }
    fclose(f);
    return true;
}

static float HalfToFloat(uint16_t h)
{
    const uint32_t sign = (uint32_t)(h & 0x8000) << 16;
    int32_t exp = (h >> 10) & 0x1F;
    uint32_t mant = h & 0x3FF, x;
    if (exp == 0) {
        if (!mant) x = sign;
        else { exp = 1; while (!(mant & 0x400)) { mant <<= 1; --exp; } mant &= 0x3FF;
               x = sign | ((uint32_t)(exp + 127 - 15) << 23) | (mant << 13); }
    } else if (exp == 31) x = sign | 0x7F800000 | (mant << 13);
    else x = sign | ((uint32_t)(exp + 127 - 15) << 23) | (mant << 13);
    float f; memcpy(&f, &x, 4); return f;
}

// HDR mode: back buffer R16G16B16A16_FLOAT, which ReShade/RenoDX treat as scRGB (linear Rec.709,
// 1.0 = 80 nits). The caller supplies colour already in that space (see dlss5_enhance.py).
static bool g_hdr = false;
static std::vector<uint16_t> g_rgbaHalf;

static bool WritePFMFromHalf(const char* path, const uint16_t* rgba, int w, int h, int pitchBytes)
{
    FILE* f = fopen(path, "wb");
    if (!f) return false;
    fprintf(f, "PF\n%d %d\n-1.0\n", w, h);
    std::vector<float> row((size_t)w * 3);
    for (int y = h - 1; y >= 0; --y) {  // PFM rows bottom-to-top
        const uint16_t* s = (const uint16_t*)((const uint8_t*)rgba + (size_t)y * pitchBytes);
        for (int x = 0; x < w; ++x)
            for (int c = 0; c < 3; ++c) row[x * 3 + c] = HalfToFloat(s[x * 4 + c]);
        fwrite(row.data(), sizeof(float), row.size(), f);
    }
    fclose(f);
    return true;
}

static uint8_t LinearToSrgb8(float v)
{
    if (!(v > 0.f)) return 0;  // also catches NaN
    const float s = v <= 0.0031308f ? v * 12.92f : 1.055f * powf(v, 1.f / 2.4f) - 0.055f;
    return (uint8_t)std::lround(std::min(s, 1.f) * 255.f);
}

// ---------------------------------------------------------------------------------------------

static const char* kShader = R"(
Texture2D<float4> gColor : register(t0);
Texture2D<float>  gDepth : register(t1);
float4 VS(uint id : SV_VertexID) : SV_Position
{
    float2 uv = float2((id << 1) & 2, id & 2);
    return float4(uv * float2(2, -2) + float2(-1, 1), 0, 1);
}
void PS(float4 pos : SV_Position, out float4 col : SV_Target, out float dep : SV_Depth)
{
    int3 p = int3(pos.xy, 0);
    col = gColor.Load(p);
    dep = gDepth.Load(p);
}
)";

static LRESULT CALLBACK WndProc(HWND h, UINT m, WPARAM w, LPARAM l)
{
    if (m == WM_CLOSE) { PostQuitMessage(0); return 0; }
    return DefWindowProcW(h, m, w, l);
}

static const char* Arg(int argc, char** argv, const char* name, const char* def)
{
    for (int i = 1; i + 1 < argc; ++i) if (strcmp(argv[i], name) == 0) return argv[i + 1];
    return def;
}

struct Job { std::string color, depth, out, mv; };

// Arnold raw motionvector PFM (pixels, x = R, y = G) -> RG16F delta UV (see top of file).
static bool LoadMV(const std::string& path, int W, int H, float scale, float signX, float signY,
                   std::vector<uint16_t>& half)
{
    Image mv;
    if (!ReadPFM(path.c_str(), mv)) return false;
    if (mv.w != W || mv.h != H || mv.c < 2) {
        fprintf(stderr, "[host] %s: need %dx%d with >=2 channels\n", path.c_str(), W, H);
        return false;
    }
    half.resize((size_t)W * H * 2);
    for (size_t i = 0; i < (size_t)W * H; ++i) {
        const float dx = mv.px[i * mv.c + 0], dy = mv.px[i * mv.c + 1];
        half[i * 2 + 0] = FloatToHalf(std::isfinite(dx) ? signX * scale * dx / W : 0.f);
        half[i * 2 + 1] = FloatToHalf(std::isfinite(dy) ? signY * scale * dy / H : 0.f);
    }
    return true;
}

// Load one frame and encode it: linear -> sRGB8 colour, camera Z -> reversed-Z depth.
static uint8_t Quantize8(float v)
{
    return v > 0.f ? (uint8_t)std::lround(std::min(v, 1.f) * 255.f) : 0;  // NaN -> 0
}

// displayReferred: colour is already view-transformed and sRGB-encoded (0..1), e.g. Arnold's
// ACES output-transform PNG; it is only quantised, not encoded again.
static bool LoadFrame(const Job& job, float nearZ, float gain, bool displayReferred, int& W, int& H,
                      std::vector<uint8_t>& rgba, std::vector<float>& rz)
{
    Image color, depth;
    if (!ReadPFM(job.color.c_str(), color) || !ReadPFM(job.depth.c_str(), depth)) return false;
    if (color.c != 3 || depth.c != 1 || color.w != depth.w || color.h != depth.h) {
        fprintf(stderr, "[host] need 3-channel colour and 1-channel depth of equal size (got %dx%dx%d, %dx%dx%d)\n",
                color.w, color.h, color.c, depth.w, depth.h, depth.c);
        return false;
    }
    if (W && (color.w != W || color.h != H)) {
        fprintf(stderr, "[host] %s is %dx%d, sequence is %dx%d\n", job.color.c_str(), color.w, color.h, W, H);
        return false;
    }
    W = color.w; H = color.h;
    rgba.resize((size_t)W * H * 4);
    if (g_hdr) g_rgbaHalf.resize((size_t)W * H * 4);
    rz.resize((size_t)W * H);
    size_t hits = 0;
    for (size_t i = 0; i < (size_t)W * H; ++i) {
        for (int ch = 0; ch < 3; ++ch) {
            const float v = color.px[i*3+ch];
            // scRGB keeps negative (out-of-Rec.709) values; they are not clipped here.
            if (g_hdr) g_rgbaHalf[i*4+ch] = FloatToHalf(std::isfinite(v) ? v * gain : 0.f);
            else rgba[i*4+ch] = displayReferred ? Quantize8(v) : LinearToSrgb8(v * gain);
        }
        rgba[i*4+3] = 255;
        if (g_hdr) g_rgbaHalf[i*4+3] = FloatToHalf(1.f);
        const float z = depth.px[i];
        float d = 0.f;  // background / miss = far = 0 in reversed-Z
        if (z > 0.f && z < 1e20f) { d = std::min(nearZ / std::max(z, nearZ), 1.f); ++hits; }
        rz[i] = d;
    }
    printf("[host] loaded %s (depth coverage %.1f%%)\n", job.color.c_str(), 100.0 * hits / ((double)W * H));
    return true;
}

int main(int argc, char** argv)
{
    const char* listPath = Arg(argc, argv, "--list", nullptr);
    const int   warmup   = atoi(Arg(argc, argv, "--warmup", "80"));
    const int   perFrame = std::max(1, atoi(Arg(argc, argv, "--per-frame", "3")));
    // Output is captured at reshade_finish_effects, before ReShade draws its startup banner, so
    // the warm-up no longer has to outlast the banner (test_fx_capture.py).
    const float warmupSec = (float)atof(Arg(argc, argv, "--warmup-sec", "1"));
    const ULONGLONG startTick = GetTickCount64();
    const float nearZ    = (float)atof(Arg(argc, argv, "--near", "0.1"));
    const float exposure = (float)atof(Arg(argc, argv, "--exposure", "0"));
    const float gain     = powf(2.f, exposure);
    const bool  display  = atoi(Arg(argc, argv, "--display-referred", "0")) != 0;
    g_hdr = atoi(Arg(argc, argv, "--hdr", "0")) != 0;
    const DXGI_FORMAT colorFmt = g_hdr ? DXGI_FORMAT_R16G16B16A16_FLOAT : DXGI_FORMAT_R8G8B8A8_UNORM;
    const UINT colorBpp = g_hdr ? 8 : 4;
    const float mvScale  = (float)atof(Arg(argc, argv, "--mv-scale", "2"));
    const float mvSignX  = (float)atof(Arg(argc, argv, "--mv-sign-x", "-1"));
    const float mvSignY  = (float)atof(Arg(argc, argv, "--mv-sign-y", "1"));

    // Register as a ReShade add-on (ReShade is our dxgi.dll, already loaded) so motion vectors
    // can be written into the feed's texture. Must happen before the swap chain exists.
    const bool addon = reshade::register_addon(GetModuleHandleW(nullptr));
    if (addon) {
        reshade::register_event<reshade::addon_event::init_effect_runtime>(OnInitRuntime);
        reshade::register_event<reshade::addon_event::destroy_effect_runtime>(OnDestroyRuntime);
        reshade::register_event<reshade::addon_event::reshade_begin_effects>(OnBeginEffects);
        reshade::register_event<reshade::addon_event::reshade_finish_effects>(OnFinishEffects);
    }
    printf("[host] ReShade add-on registration: %s\n", addon ? "ok" : "FAILED (motion vectors disabled)");

    std::vector<Job> jobs;
    if (listPath) {
        FILE* lf = fopen(listPath, "r");
        if (!lf) { fprintf(stderr, "[host] cannot open list %s\n", listPath); return 1; }
        char line[4096];
        while (fgets(line, sizeof(line), lf)) {
            std::string s(line);
            while (!s.empty() && (s.back() == '\n' || s.back() == '\r')) s.pop_back();
            std::vector<std::string> col;
            for (size_t p = 0;;) {
                const size_t t = s.find('\t', p);
                col.push_back(s.substr(p, t == std::string::npos ? std::string::npos : t - p));
                if (t == std::string::npos) break;
                p = t + 1;
            }
            if (col.size() < 3) continue;
            jobs.push_back({ col[0], col[1], col[2], col.size() > 3 ? col[3] : std::string() });
        }
        fclose(lf);
    } else {
        const char* c = Arg(argc, argv, "--color", nullptr);
        const char* d = Arg(argc, argv, "--depth", nullptr);
        if (c && d) jobs.push_back({ c, d, Arg(argc, argv, "--out", "result.ppm"), std::string() });
    }
    if (jobs.empty()) {
        fprintf(stderr, "usage: dlss5_host (--color c.pfm --depth z.pfm --out o.ppm | --list frames.txt)"
                        " [--warmup N] [--per-frame N] [--near n] [--exposure stops]\n");
        return 2;
    }

    int W = 0, H = 0;
    std::vector<uint8_t> rgba; std::vector<float> rz;
    if (!LoadFrame(jobs[0], nearZ, gain, display, W, H, rgba, rz)) return 1;
    printf("[host] %dx%d, %zu frame(s), warmup %d, %d per frame, near %.4g, exposure %+.2f stops\n",
           W, H, jobs.size(), warmup, perFrame, nearZ, exposure);

    // Window sized to the image; the back buffer is exactly WxH.
    WNDCLASSW wc = {};
    wc.lpfnWndProc = WndProc; wc.hInstance = GetModuleHandleW(nullptr);
    wc.lpszClassName = L"dlss5_host"; wc.hCursor = LoadCursor(nullptr, IDC_ARROW);
    RegisterClassW(&wc);
    RECT rc = { 0, 0, W, H };
    AdjustWindowRect(&rc, WS_OVERLAPPEDWINDOW, FALSE);
    HWND hwnd = CreateWindowW(wc.lpszClassName, L"DLSS 5 processing – closes automatically", WS_OVERLAPPEDWINDOW,
                              CW_USEDEFAULT, CW_USEDEFAULT, rc.right - rc.left, rc.bottom - rc.top,
                              nullptr, nullptr, wc.hInstance, nullptr);
    ShowWindow(hwnd, SW_SHOWNOACTIVATE);
    // Keep the helper above other windows so it is visibly working (and screen-grabbable).
    if (atoi(Arg(argc, argv, "--topmost", "1")))
        SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE);

    // Device first, then the swap chain through CreateDXGIFactory1 (imported from dxgi.dll,
    // i.e. ReShade's proxy next to this exe), the same path a D3D11 game takes.
    ID3D11Device* dev = nullptr; ID3D11DeviceContext* ctx = nullptr;
    const D3D_FEATURE_LEVEL fl[] = { D3D_FEATURE_LEVEL_11_1, D3D_FEATURE_LEVEL_11_0 };
    CHECK_HR(D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr, 0, fl, 2,
                               D3D11_SDK_VERSION, &dev, nullptr, &ctx));

    IDXGIFactory1* factory = nullptr;
    CHECK_HR(CreateDXGIFactory1(__uuidof(IDXGIFactory1), (void**)&factory));
    DXGI_SWAP_CHAIN_DESC sd = {};
    sd.BufferDesc.Width = W; sd.BufferDesc.Height = H;
    sd.BufferDesc.Format = colorFmt;
    sd.SampleDesc.Count = 1;
    sd.BufferUsage = DXGI_USAGE_RENDER_TARGET_OUTPUT;
    sd.BufferCount = 1;
    sd.OutputWindow = hwnd; sd.Windowed = TRUE;
    // Blt model: the back buffer keeps its contents after Present, so what ReShade and the
    // neural pass wrote into it during Present can be read back afterwards.
    sd.SwapEffect = DXGI_SWAP_EFFECT_SEQUENTIAL;
    IDXGISwapChain* swap = nullptr;
    CHECK_HR(factory->CreateSwapChain(dev, &sd, &swap));

    ID3D11Texture2D* back = nullptr; ID3D11RenderTargetView* rtv = nullptr;
    CHECK_HR(swap->GetBuffer(0, __uuidof(ID3D11Texture2D), (void**)&back));
    CHECK_HR(dev->CreateRenderTargetView(back, nullptr, &rtv));

    // Real depth buffer (typeless so ReShade can create an SRV on it).
    D3D11_TEXTURE2D_DESC td = {};
    td.Width = W; td.Height = H; td.MipLevels = 1; td.ArraySize = 1; td.SampleDesc.Count = 1;
    td.Format = DXGI_FORMAT_R32_TYPELESS;
    td.BindFlags = D3D11_BIND_DEPTH_STENCIL | D3D11_BIND_SHADER_RESOURCE;
    ID3D11Texture2D* dsTex = nullptr; ID3D11DepthStencilView* dsv = nullptr;
    CHECK_HR(dev->CreateTexture2D(&td, nullptr, &dsTex));
    D3D11_DEPTH_STENCIL_VIEW_DESC dvd = {};
    dvd.Format = DXGI_FORMAT_D32_FLOAT; dvd.ViewDimension = D3D11_DSV_DIMENSION_TEXTURE2D;
    CHECK_HR(dev->CreateDepthStencilView(dsTex, &dvd, &dsv));

    // Source textures (updated per frame for sequences).
    auto makeSrv = [&](DXGI_FORMAT fmt, ID3D11Texture2D** tex, ID3D11ShaderResourceView** srv) -> HRESULT {
        D3D11_TEXTURE2D_DESC d = {};
        d.Width = W; d.Height = H; d.MipLevels = 1; d.ArraySize = 1; d.SampleDesc.Count = 1;
        d.Format = fmt; d.Usage = D3D11_USAGE_DEFAULT; d.BindFlags = D3D11_BIND_SHADER_RESOURCE;
        HRESULT hr = dev->CreateTexture2D(&d, nullptr, tex);
        if (SUCCEEDED(hr)) hr = dev->CreateShaderResourceView(*tex, nullptr, srv);
        return hr;
    };
    ID3D11Texture2D* colorTex = nullptr; ID3D11Texture2D* depthTex = nullptr;
    ID3D11ShaderResourceView* colorSrv = nullptr; ID3D11ShaderResourceView* depthSrv = nullptr;
    CHECK_HR(makeSrv(colorFmt, &colorTex, &colorSrv));
    CHECK_HR(makeSrv(DXGI_FORMAT_R32_FLOAT, &depthTex, &depthSrv));
    auto upload = [&]() {
        ctx->UpdateSubresource(colorTex, 0, nullptr, g_hdr ? (const void*)g_rgbaHalf.data() : rgba.data(),
                               W * colorBpp, 0);
        ctx->UpdateSubresource(depthTex, 0, nullptr, rz.data(), W * 4, 0);
    };
    upload();

    // Shaders + state.
    ID3DBlob* vsb = nullptr; ID3DBlob* psb = nullptr; ID3DBlob* err = nullptr;
    if (FAILED(D3DCompile(kShader, strlen(kShader), "host", nullptr, nullptr, "VS", "vs_5_0", 0, 0, &vsb, &err)) ||
        FAILED(D3DCompile(kShader, strlen(kShader), "host", nullptr, nullptr, "PS", "ps_5_0", 0, 0, &psb, &err))) {
        fprintf(stderr, "[host] shader: %s\n", err ? (const char*)err->GetBufferPointer() : "?");
        return 1;
    }
    ID3D11VertexShader* vs = nullptr; ID3D11PixelShader* ps = nullptr;
    CHECK_HR(dev->CreateVertexShader(vsb->GetBufferPointer(), vsb->GetBufferSize(), nullptr, &vs));
    CHECK_HR(dev->CreatePixelShader(psb->GetBufferPointer(), psb->GetBufferSize(), nullptr, &ps));
    D3D11_DEPTH_STENCIL_DESC dsd = {};
    dsd.DepthEnable = TRUE; dsd.DepthWriteMask = D3D11_DEPTH_WRITE_MASK_ALL; dsd.DepthFunc = D3D11_COMPARISON_ALWAYS;
    ID3D11DepthStencilState* dss = nullptr;
    CHECK_HR(dev->CreateDepthStencilState(&dsd, &dss));

    // Readback target.
    D3D11_TEXTURE2D_DESC bd; back->GetDesc(&bd);
    bd.Usage = D3D11_USAGE_STAGING; bd.BindFlags = 0; bd.CPUAccessFlags = D3D11_CPU_ACCESS_READ; bd.MiscFlags = 0;
    ID3D11Texture2D* staging = nullptr;
    CHECK_HR(dev->CreateTexture2D(&bd, nullptr, &staging));
    ID3D11Texture2D* fxStaging = nullptr;
    CHECK_HR(dev->CreateTexture2D(&bd, nullptr, &fxStaging));
    g_ctx = ctx; g_back = back; g_fxStaging = fxStaging;

    // Prefer the copy taken at reshade_finish_effects (clean, no ReShade banner/overlay); fall back
    // to the post-Present back buffer if the add-on event did not fire.
    const bool fxCapture = atoi(Arg(argc, argv, "--fx-capture", "1")) != 0;
    auto capture = [&](const char* path) -> bool {
        ID3D11Texture2D* src = staging;
        if (fxCapture && g_fxCopied) src = fxStaging;
        else ctx->CopyResource(staging, back);
        D3D11_MAPPED_SUBRESOURCE map;
        if (FAILED(ctx->Map(src, 0, D3D11_MAP_READ, 0, &map))) return false;
        // HDR: float PFM (scRGB), SDR: 8-bit PPM.
        const bool ok = g_hdr ? WritePFMFromHalf(path, (const uint16_t*)map.pData, W, H, (int)map.RowPitch)
                              : WritePPM(path, (const uint8_t*)map.pData, W, H, (int)map.RowPitch);
        ctx->Unmap(src, 0);
        printf(ok ? "[host] wrote %s (%s)\n" : "[host] could not write %s (%s)\n", path,
               src == fxStaging ? "after effects, before overlay" : "post-Present");
        return ok;
    };
    // NR on/off is RenoDX's NeuralUplift setting in ReShade.ini (the caller sets it); no key press.
    const D3D11_VIEWPORT vp = { 0, 0, (float)W, (float)H, 0, 1 };
    bool quit = false;
    int presents = 0;
    auto present = [&]() -> bool {
        MSG msg;
        while (PeekMessageW(&msg, nullptr, 0, 0, PM_REMOVE)) {
            if (msg.message == WM_QUIT) quit = true;
            TranslateMessage(&msg); DispatchMessageW(&msg);
        }
        if (quit) return false;
        const float clearCol[4] = { 0, 0, 0, 1 };
        ctx->OMSetRenderTargets(1, &rtv, dsv);
        ctx->ClearRenderTargetView(rtv, clearCol);
        ctx->ClearDepthStencilView(dsv, D3D11_CLEAR_DEPTH, 0.f, 0);  // reversed-Z far
        ctx->OMSetDepthStencilState(dss, 0);
        ctx->RSSetViewports(1, &vp);
        ctx->IASetPrimitiveTopology(D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
        ctx->VSSetShader(vs, nullptr, 0);
        ctx->PSSetShader(ps, nullptr, 0);
        ID3D11ShaderResourceView* srvs[2] = { colorSrv, depthSrv };
        ctx->PSSetShaderResources(0, 2, srvs);
        ctx->Draw(3, 0);
        ID3D11ShaderResourceView* none[2] = {};
        ctx->PSSetShaderResources(0, 2, none);

        const HRESULT hr = swap->Present(1, 0);  // vsync: give ReShade/RenoDX real time to load
        if (FAILED(hr)) { fprintf(stderr, "[host] Present failed 0x%08lX at present %d\n", (unsigned long)hr, presents); return false; }
        ++presents;
        return true;
    };

    // Motion vectors: a frame's vectors describe the step from the previous frame, so they are
    // shown on that frame's first present only; the warm-up and repeated presents of the same
    // image get zero motion. Without any vectors in the list the texture is left untouched.
    bool anyMV = false;
    for (const Job& jb : jobs) anyMV |= !jb.mv.empty();
    std::vector<uint16_t> mvZero(anyMV ? (size_t)W * H * 2 : 0, 0), mvHalf;
    g_mvW = W; g_mvH = H;
    g_mvHalf = anyMV ? mvZero.data() : nullptr;
    if (anyMV) printf("[host] motion vectors: scale %g, sign (%g, %g)\n", mvScale, mvSignX, mvSignY);

    bool ok = true;
    for (size_t j = 0; j < jobs.size() && ok; ++j) {
        if (j > 0) {
            if (!LoadFrame(jobs[j], nearZ, gain, display, W, H, rgba, rz)) { ok = false; break; }
            upload();
            if (!jobs[j].mv.empty()) {
                if (!LoadMV(jobs[j].mv, W, H, mvScale, mvSignX, mvSignY, mvHalf)) { ok = false; break; }
                g_mvHalf = mvHalf.data();
            }
        }
        wchar_t title[128];
        if (jobs.size() > 1)
            swprintf(title, 128, L"DLSS 5 processing frame %zu/%zu – closes automatically", j + 1, jobs.size());
        else
            swprintf(title, 128, L"DLSS 5 processing – closes automatically");
        SetWindowTextW(hwnd, title);
        const int n = j == 0 ? warmup : perFrame;
        for (int k = 0; k < n; ++k) {
            if (!present()) { ok = false; break; }
            if (anyMV) g_mvHalf = mvZero.data();  // later presents of the same image: no motion
        }
        // ReShade draws its startup banner into the back buffer for the first seconds after
        // launch; keep presenting the first frame until that time has passed.
        if (j == 0) {
            const ULONGLONG until = startTick + (ULONGLONG)(warmupSec * 1000.f);
            while (ok && GetTickCount64() < until) if (!present()) ok = false;
        }
        if (!ok) break;
        ok = capture(jobs[j].out.c_str());
        printf("[host] frame %zu/%zu done\n", j + 1, jobs.size());
        fflush(stdout);
    }
    if (quit) fprintf(stderr, "[host] window closed after %d presents, output incomplete\n", presents);
    if (anyMV) printf("[host] motion vector uploads into texMotionVectors: %d of %d presents\n", g_mvUploads, presents);
    g_mvHalf = nullptr;
    if (addon) reshade::unregister_addon(GetModuleHandleW(nullptr));

    g_ctx = nullptr; g_back = nullptr; g_fxStaging = nullptr;
    SafeRelease(fxStaging); SafeRelease(staging); SafeRelease(dss); SafeRelease(ps); SafeRelease(vs);
    SafeRelease(psb); SafeRelease(vsb); SafeRelease(err);
    SafeRelease(depthSrv); SafeRelease(colorSrv); SafeRelease(depthTex); SafeRelease(colorTex);
    SafeRelease(dsv); SafeRelease(dsTex);
    SafeRelease(rtv); SafeRelease(back); SafeRelease(swap); SafeRelease(factory);
    SafeRelease(ctx); SafeRelease(dev);
    DestroyWindow(hwnd);
    return quit ? 3 : ok ? 0 : 1;
}

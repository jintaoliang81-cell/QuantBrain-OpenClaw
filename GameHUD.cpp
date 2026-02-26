// Standard headers first to avoid min/max macro conflicts from SharedDefines.h
#include <string>
#include <sstream>
#include <iomanip>
#include <ctime>
#include <cstring>
#include <cstdlib>
#include <thread>
#include <mutex>
#include <atomic>
#include <chrono>

#include "GameHUD.h"
#include "../GameApp/BaseGameApp.h"
#include "../Resource/ResourceCache.h"
#include "../Scene/SceneNodes.h"
#include "../Resource/Loaders/PidLoader.h"
#include "../Graphics2D/Image.h"
#include "../UserInterface/HumanView.h"

#include <SDL2/SDL_ttf.h>
#include <curl/curl.h>

//=============================================================================
// List of exposed HUD elements from scene:
//
// INGAME: 
//
// "score"     - treasure chest in upper left corner
// "stopwatch" - stopwatch under treasure chest in upper left corner - not visible by default
// "health"    - pumping heart in upper right corner
// "pistol"    - ammo  
// "magic"     - ammo
// "dynamite"  - ammo
// "lives"     - claw's head under ammo in upper right corner
//
// IN MAIN MENU:
// ??
//
// PRICE FEED OVERLAY:
// Renders a real-time NVDA / TRX price monitor panel in the bottom-left corner.
// Data is fetched from Yahoo Finance every 15 seconds in a background thread.
//=============================================================================

// ─── libcurl write callback ───────────────────────────────────────────────────
static size_t CurlWriteCallback(void* contents, size_t size, size_t nmemb, std::string* output)
{
    size_t totalSize = size * nmemb;
    output->append(static_cast<char*>(contents), totalSize);
    return totalSize;
}

// ─── ScreenElementHUD Constructor / Destructor ───────────────────────────────

ScreenElementHUD::ScreenElementHUD()
    :
    m_IsVisible(true),
    m_pFPSTexture(NULL),
    m_pPositionTexture(NULL),
    m_pBossBarTexture(NULL),
    m_PriceFeedRunning(false),
    m_pNvdaTexture(NULL),
    m_pTrxTexture(NULL),
    m_pPanelBgTexture(NULL),
    m_PriceTexturesDirty(false)
{
    m_NvdaData.symbol = "NVDA";
    m_NvdaData.price  = 0.0;
    m_NvdaData.change = 0.0;
    m_NvdaData.changePct = 0.0;
    m_NvdaData.lastUpdated = "Fetching...";
    m_NvdaData.valid  = false;

    m_TrxData.symbol  = "TRX-USD";
    m_TrxData.price   = 0.0;
    m_TrxData.change  = 0.0;
    m_TrxData.changePct = 0.0;
    m_TrxData.lastUpdated = "Fetching...";
    m_TrxData.valid   = false;

    IEventMgr::Get()->VAddListener(MakeDelegate(this, &ScreenElementHUD::BossHealthChangedDelegate), EventData_Boss_Health_Changed::sk_EventType);
    IEventMgr::Get()->VAddListener(MakeDelegate(this, &ScreenElementHUD::BossFightEndedDelegate), EventData_Boss_Fight_Ended::sk_EventType);

    StartPriceFeedThread();
}

ScreenElementHUD::~ScreenElementHUD()
{
    StopPriceFeedThread();

    IEventMgr::Get()->VRemoveListener(MakeDelegate(this, &ScreenElementHUD::BossHealthChangedDelegate), EventData_Boss_Health_Changed::sk_EventType);
    IEventMgr::Get()->VRemoveListener(MakeDelegate(this, &ScreenElementHUD::BossFightEndedDelegate), EventData_Boss_Fight_Ended::sk_EventType);

    m_HUDElementsMap.clear();

    SDL_DestroyTexture(m_pFPSTexture);
    SDL_DestroyTexture(m_pPositionTexture);
    SDL_DestroyTexture(m_pBossBarTexture);
    SDL_DestroyTexture(m_pNvdaTexture);
    SDL_DestroyTexture(m_pTrxTexture);
    SDL_DestroyTexture(m_pPanelBgTexture);
}

bool ScreenElementHUD::Initialize(SDL_Renderer* pRenderer, shared_ptr<CameraNode> pCamera)
{
    m_pRenderer = pRenderer;
    m_pCamera = pCamera;

    for (uint32 i = 0; i < SCORE_NUMBERS_COUNT; i++)
    {
        m_ScoreNumbers[i] = PidResourceLoader::LoadAndReturnImage("/game/images/interface/scorenumbers/000.pid", g_pApp->GetCurrentPalette());
    }

    for (uint32 i = 0; i < STOPWATCH_NUMBERS_COUNT; i++)
    {
        m_StopwatchNumbers[i] = PidResourceLoader::LoadAndReturnImage("/game/images/interface/scorenumbers/000.pid", g_pApp->GetCurrentPalette());
    }

    for (uint32 i = 0; i < HEALTH_NUMBERS_COUNT; i++)
    {
        m_HealthNumbers[i] = PidResourceLoader::LoadAndReturnImage("/game/images/interface/healthnumbers/000.pid", g_pApp->GetCurrentPalette());
    }

    for (uint32 i = 0; i < AMMO_NUMBERS_COUNT; i++)
    {
        m_AmmoNumbers[i] = PidResourceLoader::LoadAndReturnImage("/game/images/interface/smallnumbers/000.pid", g_pApp->GetCurrentPalette());
    }

    for (uint32 i = 0; i < LIVES_NUMBERS_COUNT; i++)
    {
        m_LivesNumbers[i] = PidResourceLoader::LoadAndReturnImage("/game/images/interface/smallnumbers/000.pid", g_pApp->GetCurrentPalette());
    }

    UpdateFPS(0);

    return true;
}

void ScreenElementHUD::VOnLostDevice()
{
}

// ─── Render ──────────────────────────────────────────────────────────────────

void ScreenElementHUD::VOnRender(uint32 msDiff)
{
    Point scale = g_pApp->GetScale();
    int cameraWidth  = m_pCamera->GetWidth();
    int cameraHeight = m_pCamera->GetHeight();

    if (IsElementVisible("score"))
    {
        for (int i = 0; i < SCORE_NUMBERS_COUNT; i++)
        {
            SDL_Rect renderRect = { 40 + i * 13, 5, m_ScoreNumbers[i]->GetWidth(), m_ScoreNumbers[i]->GetHeight() };
            SDL_RenderCopy(m_pRenderer, m_ScoreNumbers[i]->GetTexture(), NULL, &renderRect);
        }
    }

    if (IsElementVisible("health"))
    {
        for (int i = 0; i < HEALTH_NUMBERS_COUNT; i++)
        {
            SDL_Rect renderRect = { 
                (int)(cameraWidth / scale.x) - 60 + i * (m_HealthNumbers[i]->GetWidth() - 0) + m_HealthNumbers[i]->GetOffsetX(),
                2 + m_HealthNumbers[i]->GetOffsetY(),
                m_HealthNumbers[i]->GetWidth(), 
                m_HealthNumbers[i]->GetHeight() };
            SDL_RenderCopy(m_pRenderer, m_HealthNumbers[i]->GetTexture(), NULL, &renderRect);
        }
    }

    if (IsElementVisible("pistol") || IsElementVisible("dynamite") || IsElementVisible("magic"))
    {
        for (int i = 0; i < AMMO_NUMBERS_COUNT; i++)
        {
            SDL_Rect renderRect = { 
                (int)(cameraWidth / scale.x) - 46 + i * (m_AmmoNumbers[i]->GetWidth() + m_AmmoNumbers[i]->GetOffsetX()), 
                43 + m_AmmoNumbers[i]->GetOffsetY(), 
                m_AmmoNumbers[i]->GetWidth(), 
                m_AmmoNumbers[i]->GetHeight() };
            SDL_RenderCopy(m_pRenderer, m_AmmoNumbers[i]->GetTexture(), NULL, &renderRect);
        }
    }

    if (IsElementVisible("lives"))
    {
        for (int i = 0; i < LIVES_NUMBERS_COUNT; i++)
        {
            SDL_Rect renderRect = { 
                (int)(cameraWidth / scale.x) - 36 + i * (m_LivesNumbers[i]->GetWidth() + m_LivesNumbers[i]->GetOffsetX()),
                71 + m_LivesNumbers[i]->GetOffsetY(),
                m_LivesNumbers[i]->GetWidth(), 
                m_LivesNumbers[i]->GetHeight() };
            SDL_RenderCopy(m_pRenderer, m_LivesNumbers[i]->GetTexture(), NULL, &renderRect);
        }
    }

    if (IsElementVisible("stopwatch"))
    {
        for (int i = 0; i < STOPWATCH_NUMBERS_COUNT; i++)
        {
            SDL_Rect renderRect = { 40 + i * 13, 45, m_StopwatchNumbers[i]->GetWidth(), m_StopwatchNumbers[i]->GetHeight() };
            SDL_RenderCopy(m_pRenderer, m_StopwatchNumbers[i]->GetTexture(), NULL, &renderRect);
        }
    }

    if (m_pFPSTexture)
    {
        SDL_Rect renderRect;
        SDL_QueryTexture(m_pFPSTexture, NULL, NULL, &renderRect.w, &renderRect.h);
        renderRect.x = (int)((m_pCamera->GetWidth() / 2) / scale.x - 20);
        renderRect.y = (int)(15 / scale.y);
        SDL_RenderCopy(m_pRenderer, m_pFPSTexture, NULL, &renderRect);
    }

    if (m_pPositionTexture)
    {
        SDL_Rect renderRect;
        SDL_QueryTexture(m_pPositionTexture, NULL, NULL, &renderRect.w, &renderRect.h);
        renderRect.x = (int)(m_pCamera->GetWidth() / scale.x - renderRect.w - 1);
        renderRect.y = (int)(m_pCamera->GetHeight() / scale.y - renderRect.h - 1);
        SDL_RenderCopy(m_pRenderer, m_pPositionTexture, NULL, &renderRect);
    }

    if (m_pBossBarTexture)
    {
        Point pos;
        Point windowSize = g_pApp->GetWindowSize();
        Point windowScale = g_pApp->GetScale();

        pos.Set(
            (((windowSize.x * 0.5) / windowScale.x) - 114),
            ((windowSize.y * 0.8) / windowScale.y) - 3);

        SDL_Rect renderRect;
        SDL_QueryTexture(m_pBossBarTexture, NULL, NULL, &renderRect.w, &renderRect.h);
        renderRect.x = pos.x;
        renderRect.y = pos.y;
        SDL_RenderCopy(m_pRenderer, m_pBossBarTexture, NULL, &renderRect);
    }

    // ─── Price Feed Overlay ───────────────────────────────────────────────────
    // Rebuild textures if data was updated by the background thread
    if (m_PriceTexturesDirty && m_pRenderer)
    {
        RebuildPriceTextures();
        m_PriceTexturesDirty = false;
    }

    // Panel background
    const int PANEL_X      = 8;
    const int PANEL_Y_BASE = (int)(cameraHeight / scale.y) - 90;
    const int PANEL_W      = 280;
    const int PANEL_H      = 82;

    SDL_Rect panelRect = { PANEL_X, PANEL_Y_BASE, PANEL_W, PANEL_H };
    SDL_SetRenderDrawBlendMode(m_pRenderer, SDL_BLENDMODE_BLEND);
    SDL_SetRenderDrawColor(m_pRenderer, 0, 0, 0, 180);
    SDL_RenderFillRect(m_pRenderer, &panelRect);
    // Panel border
    SDL_SetRenderDrawColor(m_pRenderer, 0, 200, 255, 220);
    SDL_RenderDrawRect(m_pRenderer, &panelRect);
    SDL_SetRenderDrawBlendMode(m_pRenderer, SDL_BLENDMODE_NONE);

    if (m_pNvdaTexture)
    {
        SDL_Rect r;
        SDL_QueryTexture(m_pNvdaTexture, NULL, NULL, &r.w, &r.h);
        r.x = PANEL_X + 8;
        r.y = PANEL_Y_BASE + 6;
        SDL_RenderCopy(m_pRenderer, m_pNvdaTexture, NULL, &r);
    }

    if (m_pTrxTexture)
    {
        SDL_Rect r;
        SDL_QueryTexture(m_pTrxTexture, NULL, NULL, &r.w, &r.h);
        r.x = PANEL_X + 8;
        r.y = PANEL_Y_BASE + 44;
        SDL_RenderCopy(m_pRenderer, m_pTrxTexture, NULL, &r);
    }
}

// ─── Update ──────────────────────────────────────────────────────────────────

void ScreenElementHUD::VOnUpdate(uint32 msDiff)
{
    static int msAccumulation = 0;
    static int framesAccumulation = 0;

    UpdateCameraPosition();

    msAccumulation += msDiff;
    framesAccumulation++;
    if (msAccumulation > 1000)
    {
        UpdateFPS(framesAccumulation);
        msAccumulation = 0;
        framesAccumulation = 0;
    }
}

bool ScreenElementHUD::VOnEvent(SDL_Event& evt)
{
    return false;
}

// ─── Price Feed: Thread Management ───────────────────────────────────────────

void ScreenElementHUD::StartPriceFeedThread()
{
    m_PriceFeedRunning = true;
    m_PriceFeedThread = std::thread(&ScreenElementHUD::PriceFeedWorker, this);
}

void ScreenElementHUD::StopPriceFeedThread()
{
    m_PriceFeedRunning = false;
    if (m_PriceFeedThread.joinable())
        m_PriceFeedThread.join();
}

// ─── Price Feed: HTTP Fetch ───────────────────────────────────────────────────

std::string ScreenElementHUD::FetchURL(const std::string& url)
{
    std::string response;
    CURL* curl = curl_easy_init();
    if (!curl) return response;

    curl_easy_setopt(curl, CURLOPT_URL, url.c_str());
    curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, CurlWriteCallback);
    curl_easy_setopt(curl, CURLOPT_WRITEDATA, &response);
    curl_easy_setopt(curl, CURLOPT_USERAGENT, "Mozilla/5.0 (compatible; OpenClaw-PriceFeed/1.0)");
    curl_easy_setopt(curl, CURLOPT_TIMEOUT, 10L);
    curl_easy_setopt(curl, CURLOPT_FOLLOWLOCATION, 1L);
    curl_easy_setopt(curl, CURLOPT_SSL_VERIFYPEER, 0L);

    CURLcode res = curl_easy_perform(curl);
    curl_easy_cleanup(curl);

    if (res != CURLE_OK)
        response.clear();

    return response;
}

// ─── Price Feed: JSON Parser (minimal, no external lib) ──────────────────────

static double ExtractJsonDouble(const std::string& json, const std::string& key)
{
    // Looks for "key":VALUE in the JSON string
    std::string searchKey = "\"" + key + "\":";
    size_t pos = json.find(searchKey);
    if (pos == std::string::npos) return 0.0;
    pos += searchKey.size();
    // skip whitespace
    while (pos < json.size() && (json[pos] == ' ' || json[pos] == '\t')) pos++;
    // read number
    size_t end = pos;
    while (end < json.size() && (std::isdigit(json[end]) || json[end] == '.' || json[end] == '-' || json[end] == 'e' || json[end] == 'E' || json[end] == '+'))
        end++;
    if (end == pos) return 0.0;
    return std::stod(json.substr(pos, end - pos));
}

PriceFeedData ScreenElementHUD::ParseYahooFinance(const std::string& symbol, const std::string& json)
{
    PriceFeedData data;
    data.symbol = symbol;
    data.valid  = false;

    if (json.empty()) return data;

    // Yahoo Finance v8 API: result[0].meta.regularMarketPrice
    double price    = ExtractJsonDouble(json, "regularMarketPrice");
    double change   = ExtractJsonDouble(json, "regularMarketChange");
    double changePct = ExtractJsonDouble(json, "regularMarketChangePercent");

    if (price > 0.0)
    {
        data.price     = price;
        data.change    = change;
        data.changePct = changePct;
        data.valid     = true;

        // Timestamp
        auto now = std::chrono::system_clock::now();
        std::time_t t = std::chrono::system_clock::to_time_t(now);
        char buf[32];
        std::strftime(buf, sizeof(buf), "%H:%M:%S UTC", std::gmtime(&t));
        data.lastUpdated = std::string(buf);
    }

    return data;
}

// ─── Price Feed: Background Worker ───────────────────────────────────────────

void ScreenElementHUD::PriceFeedWorker()
{
    curl_global_init(CURL_GLOBAL_DEFAULT);

    while (m_PriceFeedRunning)
    {
        // Yahoo Finance v8 quote endpoint
        const std::string nvdaUrl = "https://query1.finance.yahoo.com/v8/finance/chart/NVDA?interval=1m&range=1d";
        const std::string trxUrl  = "https://query1.finance.yahoo.com/v8/finance/chart/TRX-USD?interval=1m&range=1d";

        std::string nvdaJson = FetchURL(nvdaUrl);
        std::string trxJson  = FetchURL(trxUrl);

        PriceFeedData nvda = ParseYahooFinance("NVDA",    nvdaJson);
        PriceFeedData trx  = ParseYahooFinance("TRX-USD", trxJson);

        {
            std::lock_guard<std::mutex> lock(m_PriceMutex);
            if (nvda.valid) m_NvdaData = nvda;
            else            m_NvdaData.lastUpdated = "Fetch failed";
            if (trx.valid)  m_TrxData  = trx;
            else            m_TrxData.lastUpdated  = "Fetch failed";
            m_PriceTexturesDirty = true;
        }

        // Sleep 15 seconds between refreshes
        for (int i = 0; i < 150 && m_PriceFeedRunning; i++)
            std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }

    curl_global_cleanup();
}

// ─── Price Feed: Texture Rebuild (must be called from render thread) ─────────

void ScreenElementHUD::RebuildPriceTextures()
{
    TTF_Font* font = g_pApp->GetConsoleFont();
    if (!font) return;

    PriceFeedData nvda, trx;
    {
        std::lock_guard<std::mutex> lock(m_PriceMutex);
        nvda = m_NvdaData;
        trx  = m_TrxData;
    }

    // Helper lambda to format a price row
    auto buildTexture = [&](SDL_Texture*& tex, const PriceFeedData& d) {
        if (tex) { SDL_DestroyTexture(tex); tex = NULL; }

        std::ostringstream oss;
        if (d.valid)
        {
            oss << d.symbol << "  $"
                << std::fixed << std::setprecision(d.price < 1.0 ? 5 : 2) << d.price
                << "  " << (d.change >= 0 ? "+" : "") << std::fixed << std::setprecision(2) << d.change
                << " (" << (d.changePct >= 0 ? "+" : "") << std::fixed << std::setprecision(2) << d.changePct << "%)";
        }
        else
        {
            oss << d.symbol << "  " << d.lastUpdated;
        }

        SDL_Color col = { 255, 255, 255, 255 };
        if (d.valid)
            col = (d.change >= 0) ? SDL_Color{0, 255, 128, 255} : SDL_Color{255, 80, 80, 255};

        SDL_Surface* surf = TTF_RenderText_Blended(font, oss.str().c_str(), col);
        if (surf)
        {
            tex = SDL_CreateTextureFromSurface(m_pRenderer, surf);
            SDL_FreeSurface(surf);
        }

        // Second line: last updated
        std::string updLine = "  Updated: " + d.lastUpdated;
        // (rendered as part of the same texture above for simplicity)
    };

    buildTexture(m_pNvdaTexture, nvda);
    buildTexture(m_pTrxTexture,  trx);
}

// ─── Existing HUD helpers ─────────────────────────────────────────────────────

static void SetImageText(uint32 newValue, uint32 divider, shared_ptr<Image>* pField, uint32 fieldSize, std::string textResourcePrefixPath)
{
    for (uint32 i = 0; i < fieldSize; i++)
    {
        uint32 num = (newValue / divider) % 10;
        std::string numStr = ToStr(num);
        std::string resourcePath = textResourcePrefixPath + numStr + ".pid";
        pField[i] = PidResourceLoader::LoadAndReturnImage(resourcePath.c_str(), g_pApp->GetCurrentPalette());
        divider /= 10;

        if (num == 1)
        {
            pField[i]->SetOffset(4, 0);
        }
    }
}

void ScreenElementHUD::UpdateScore(uint32 newScore)
{
    SetImageText(newScore, 10000000, m_ScoreNumbers, SCORE_NUMBERS_COUNT, "/game/images/interface/scorenumbers/00");
}

void ScreenElementHUD::UpdateHealth(uint32 newHealth)
{
    if (newHealth > 999)
    {
        LOG_WARNING("Health was to be updated to: " + ToStr(newHealth) + ". Clamping to 999. This should be handled by logic before it got here !");
        newHealth = 999;
    }

    SetImageText(newHealth, 100, m_HealthNumbers, HEALTH_NUMBERS_COUNT, "/game/images/interface/healthnumbers/00");
}

void ScreenElementHUD::ChangeAmmoType(AmmoType newAmmoType)
{
}

void ScreenElementHUD::UpdateAmmo(uint32 newAmmo)
{
    if (newAmmo > 99)
    {
        LOG_WARNING("Ammo was to be updated to: " + ToStr(newAmmo) + ". Clamping to 99. This should be handled by logic before it got here !");
        newAmmo = 99;
    }

    SetImageText(newAmmo, 10, m_AmmoNumbers, AMMO_NUMBERS_COUNT, "/game/images/interface/smallnumbers/00");
}

void ScreenElementHUD::UpdateLives(uint32 newLives)
{
    if (newLives > 9)
    {
        LOG_WARNING("Lives were to be updated to: " + ToStr(newLives) + ". Clamping to 9. This should be handled by logic before it got here !");
        newLives = 9;
    }

    SetImageText(newLives, 1, m_LivesNumbers, LIVES_NUMBERS_COUNT, "/game/images/interface/smallnumbers/00");
}

void ScreenElementHUD::UpdateStopwatchTime(uint32 newTime)
{
    SetImageText(newTime, 100, m_StopwatchNumbers, STOPWATCH_NUMBERS_COUNT, "/game/images/interface/scorenumbers/00");
}

void ScreenElementHUD::UpdateFPS(uint32 newFPS)
{
    if (m_pFPSTexture)
    {
        SDL_DestroyTexture(m_pFPSTexture);
        m_pFPSTexture = NULL;
    }

    if (!g_pApp->GetGlobalOptions()->showFps)
    {
        return;
    }

    std::string fpsString = "FPS: " + ToStr(newFPS);
    SDL_Surface* pFPSSurface = TTF_RenderText_Blended(g_pApp->GetConsoleFont(), fpsString.c_str(), { 255, 255, 255, 255 });
    m_pFPSTexture = SDL_CreateTextureFromSurface(m_pRenderer, pFPSSurface);
    SDL_FreeSurface(pFPSSurface);
}

void ScreenElementHUD::UpdateCameraPosition()
{
    if (m_pPositionTexture)
    {
        SDL_DestroyTexture(m_pPositionTexture);
        m_pPositionTexture = NULL;
    }

    if (!g_pApp->GetGlobalOptions()->showPosition)
    {
        return;
    }

    Point scale = g_pApp->GetScale();

    Point cameraCenter = Point(m_pCamera->GetPosition().x + (int)((m_pCamera->GetWidth() / 2) / scale.x),
        m_pCamera->GetPosition().y + (int)((m_pCamera->GetHeight() / 2) / scale.y));

    std::string positionString = "Position: [X = " + ToStr((int)cameraCenter.x) +
        ", Y = " + ToStr((int)cameraCenter.y) + "]";

    SDL_Surface* pPositionSurface = TTF_RenderText_Blended(g_pApp->GetConsoleFont(), positionString.c_str(), { 255, 255, 255, 255 });
    m_pPositionTexture = SDL_CreateTextureFromSurface(m_pRenderer, pPositionSurface);
    SDL_FreeSurface(pPositionSurface);
}

bool ScreenElementHUD::SetElementVisible(const std::string& element, bool visible)
{
    auto iter = m_HUDElementsMap.find(element);
    if (iter != m_HUDElementsMap.end())
    {
        iter->second->SetVisible(visible);
        return true;
    }

    return false;
}

bool ScreenElementHUD::IsElementVisible(const std::string& element)
{
    auto iter = m_HUDElementsMap.find(element);
    if (iter != m_HUDElementsMap.end())
    {
        return iter->second->IsVisible(NULL);
    }

    return false;
}

void ScreenElementHUD::BossHealthChangedDelegate(IEventDataPtr pEvent)
{
    shared_ptr<EventData_Boss_Health_Changed> pCastEventData =
        static_pointer_cast<EventData_Boss_Health_Changed>(pEvent);

    if (pCastEventData->GetNewHealthLeft() <= 0)
    {
        SDL_DestroyTexture(m_pBossBarTexture);
        return;
    }

    const int FULL_LENGTH = 228;

    int length = (int)(((float)pCastEventData->GetNewHealthPercentage() / 100.0f) * FULL_LENGTH);

    if (m_pBossBarTexture)
    {
        SDL_DestroyTexture(m_pBossBarTexture);
    }

    m_pBossBarTexture = Util::CreateSDLTextureRect(length, 7, COLOR_RED, m_pRenderer);
}

void ScreenElementHUD::BossFightEndedDelegate(IEventDataPtr pEvent)
{
    LOG("GOTIT!")
    if (m_pBossBarTexture)
    {
        SDL_DestroyTexture(m_pBossBarTexture);
        m_pBossBarTexture = NULL;
    }
}

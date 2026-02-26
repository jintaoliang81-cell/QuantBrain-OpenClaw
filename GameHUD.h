#ifndef __GAMEHUD_H__
#define __GAMEHUD_H__

// ─── C++ standard headers MUST come before SharedDefines.h
//     because SharedDefines.h defines a min/max macro that conflicts
//     with std::numeric_limits and std::chrono.
#include <string>
#include <thread>
#include <mutex>
#include <atomic>

#include "../Interfaces.h"
#include "../SharedDefines.h"
#include "../Scene/HUDSceneNode.h"

const uint32 SCORE_NUMBERS_COUNT = 8;
const uint32 HEALTH_NUMBERS_COUNT = 3;
const uint32 AMMO_NUMBERS_COUNT = 2;
const uint32 LIVES_NUMBERS_COUNT = 1;
const uint32 STOPWATCH_NUMBERS_COUNT = 3;

typedef std::map<std::string, shared_ptr<SDL2HUDSceneNode>> HUDElementsMap;

// ─── Price Feed Data ──────────────────────────────────────────────────────────
struct PriceFeedData {
    std::string symbol;
    double      price;
    double      change;
    double      changePct;
    std::string lastUpdated;
    bool        valid;
};

class Image;
class CameraNode;
class ScreenElementHUD : public IScreenElement
{
public:
    ScreenElementHUD();
    virtual ~ScreenElementHUD();
    
    bool Initialize(SDL_Renderer* pRenderer, shared_ptr<CameraNode> pCamera);

    virtual void VOnLostDevice() override;
    virtual void VOnRender(uint32 msDiff) override;
    virtual void VOnUpdate(uint32 msDiff) override;

    virtual int32_t VGetZOrder() const override { return 9000; }
    virtual void VSetZOrder(int32 const zOrder) override { }
    virtual bool VIsVisible() override { return m_IsVisible; }
    virtual void VSetVisible(bool visible) override { m_IsVisible = visible; }

    virtual bool VOnEvent(SDL_Event& evt) override;

    void AddHUDElement(const std::string& key, const shared_ptr<SDL2HUDSceneNode>& pHUDSceneNode) { m_HUDElementsMap[key] = pHUDSceneNode; }

    bool SetElementVisible(const std::string& element, bool visible);
    bool IsElementVisible(const std::string& element);

    void UpdateScore(uint32 newScore);
    void UpdateHealth(uint32 newHealth);
    void ChangeAmmoType(AmmoType newAmmoType);
    void UpdateAmmo(uint32 newAmmo);
    void UpdateLives(uint32 newLives);
    void UpdateStopwatchTime(uint32 newTime);

    void UpdateFPS(uint32 newFPS);

private:
    void BossHealthChangedDelegate(IEventDataPtr pEvent);
    void BossFightEndedDelegate(IEventDataPtr pEvent);

    void UpdateCameraPosition();

    // ─── Price Feed Methods ───────────────────────────────────────────────────
    void StartPriceFeedThread();
    void StopPriceFeedThread();
    void PriceFeedWorker();
    static std::string FetchURL(const std::string& url);
    static PriceFeedData ParseYahooFinance(const std::string& symbol, const std::string& json);
    void RebuildPriceTextures();

    bool m_IsVisible;
    shared_ptr<Image> m_ScoreNumbers[SCORE_NUMBERS_COUNT];
    shared_ptr<Image> m_HealthNumbers[HEALTH_NUMBERS_COUNT];
    shared_ptr<Image> m_AmmoNumbers[AMMO_NUMBERS_COUNT];
    shared_ptr<Image> m_LivesNumbers[LIVES_NUMBERS_COUNT];
    shared_ptr<Image> m_StopwatchNumbers[STOPWATCH_NUMBERS_COUNT];

    SDL_Renderer* m_pRenderer;
    shared_ptr<CameraNode> m_pCamera;

    HUDElementsMap m_HUDElementsMap;

    SDL_Texture* m_pFPSTexture;
    SDL_Texture* m_pPositionTexture;
    SDL_Texture* m_pBossBarTexture;

    // ─── Price Feed State ─────────────────────────────────────────────────────
    std::thread          m_PriceFeedThread;
    std::mutex           m_PriceMutex;
    std::atomic<bool>    m_PriceFeedRunning;
    PriceFeedData        m_NvdaData;
    PriceFeedData        m_TrxData;
    SDL_Texture*         m_pNvdaTexture;
    SDL_Texture*         m_pTrxTexture;
    SDL_Texture*         m_pPanelBgTexture;
    bool                 m_PriceTexturesDirty;
};

#endif

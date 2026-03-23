/**
 * PyWebview API 通信层封装
 * 提供浏览器开发环境下的安全调用和 Mock 数据支持
 */

/**
 * 检测是否在 pywebview 环境
 */
function isPywebviewAvailable() {
  return typeof window !== 'undefined' &&
         typeof window.pywebview !== 'undefined' &&
         typeof window.pywebview.api !== 'undefined'
}

/**
 * 安全调用 pywebview API
 * @param {string} methodName - API 方法名
 * @param {Array} args - 参数列表
 * @param {any} mockData - Mock 数据（浏览器环境下返回）
 * @returns {Promise<any>}
 */
async function safeApiCall(methodName, args = [], mockData = null) {
  try {
    if (!isPywebviewAvailable()) {
      console.warn(`[usePyWebview] ${methodName}() called in browser environment, returning mock data`)
      return mockData
    }

    const method = window.pywebview.api[methodName]
    if (typeof method !== 'function') {
      console.warn(`[usePyWebview] ${methodName} is not a function`)
      return mockData
    }

    return await method.apply(null, args)
  } catch (error) {
    console.error(`[usePyWebview] ${methodName}() failed:`, error)
    return mockData
  }
}

export function usePyWebview() {
  return {
    // ==================== 环境检测 ====================
    isPywebviewAvailable,

    // ==================== 配置相关 API ====================

    /**
     * 加载配置
     */
    async loadConfig() {
      return safeApiCall('load_config', [], {
        status: 'success',
        baseUrl: 'https://api.deepseek.com/v1',
        apiKey: '',
        modelName: 'deepseek-chat',
        prompt: '',
        autoStart: false,
        muteMode: false,
        trackMode: 'continuous',
        fontFamily: 'sans-serif',
        customFontPath: '',
        customFontName: '',
        subscribedSources: [],
        pollingInterval: 900,
        isLocked: false,
        apiBalanceOk: true,
        configSign: '',
        lastCloudSyncTime: 0,
        deviceId: 'browser-device',
      })
    },

    /**
     * 保存配置
     */
    async saveConfig(config) {
      return safeApiCall('save_config', [config], {
        status: 'success',
        message: '保存成功（Mock）',
      })
    },

    /**
     * 测试 AI 连接
     */
    async testAiConnection(baseUrl, apiKey, modelName) {
      return safeApiCall('test_ai_connection', [baseUrl, apiKey, modelName], {
        status: 'success',
        data: { connected: false, latency: 999, message: 'Mock 测试' },
      })
    },

    // ==================== 数据查询 API ====================

    /**
     * 分页获取历史文章
     */
    async getHistoryPaged(page, pageSize, source = null, sourceNames = null) {
      return safeApiCall('get_history_paged', [page, pageSize, source, sourceNames], {
        status: 'success',
        data: [],
        hasMore: false,
      })
    },

    /**
     * 搜索文章
     */
    async searchArticles(keyword, source = null) {
      return safeApiCall('search_articles', [keyword, source], {
        status: 'success',
        data: [],
      })
    },

    /**
     * 获取所有来源
     */
    async getAllSources() {
      return safeApiCall('get_all_sources', [], {
        status: 'success',
        data: ['公文通', '中德智能制造学院', '人工智能学院'],
      })
    },

    /**
     * 切换收藏状态
     */
    async toggleFavorite(url) {
      return safeApiCall('toggle_favorite', [url], {
        status: 'success',
        is_favorite: false,
      })
    },

    // ==================== 更新与版本 API ====================

    /**
     * 检查软件更新
     */
    async checkSoftwareUpdate(forceRefresh = false) {
      return safeApiCall('check_software_update', [forceRefresh], {
        status: 'success',
        hasUpdate: false,
        currentVersion: '1.0.0',
        latestVersion: '1.0.0',
        releaseDate: '2024-03-20',
        releaseNotes: '',
        updateAvailable: false,
      })
    },

    /**
     * 获取版本信息
     */
    async getVersionInfo() {
      return safeApiCall('get_version_info', [], {
        status: 'success',
        version: '1.0.0',
        releaseDate: '2024-03-20',
        changelog: [],
      })
    },

    // ==================== API 余额相关 API ====================

    /**
     * 获取 API 余额状态
     */
    async getApiBalanceStatus() {
      return safeApiCall('get_api_balance_status', [], {
        status: 'success',
        balanceOk: true,
        balanceAmount: 100,
        balanceLevel: 'normal',
      })
    },

    /**
     * 清除 API 余额状态
     */
    async clearApiBalanceStatus() {
      return safeApiCall('clear_api_balance_status', [], null)
    },

    // ==================== 字体相关 API ====================

    /**
     * 导入自定义字体
     */
    async importCustomFont() {
      return safeApiCall('import_custom_font', [], {
        status: 'success',
        success: false,
        path: '',
        error: '请在桌面应用中导入字体文件',
      })
    },

    // ==================== 文章操作 API ====================

    /**
     * 下载附件
     */
    async downloadAttachment(url, filename) {
      return safeApiCall('download_attachment', [url, filename], {
        status: 'success',
        success: false,
        path: '',
      })
    },

    /**
     * 保存快照
     */
    async saveSnapshot(base64image, title) {
      return safeApiCall('save_snapshot', [base64image, title], {
        status: 'success',
        data: {
          success: false,
          path: '/mock/snapshot.png',
        },
      })
    },

    /**
     * 复制图片到剪贴板
     */
    async copyImageToClipboard(imagePath) {
      return safeApiCall('copy_image_to_clipboard', [imagePath], null)
    },

    /**
     * 重新生成摘要
     */
    async regenerateSummary(articleId) {
      return safeApiCall('regenerate_summary', [articleId], {
        status: 'success',
      })
    },

    // ==================== 更新相关 API ====================

    /**
     * 检查更新
     */
    async checkUpdates(isManual = true) {
      return safeApiCall('check_updates', [isManual], {
        status: 'success',
        data: [],
        submittedCount: 0,
        cooldownRemaining: 0,
      })
    },

    /**
     * 获取更新冷却时间
     */
    async getUpdateCooldown() {
      return safeApiCall('get_update_cooldown', [], 0)
    },

    /**
     * 取消 AI 任务
     */
    async cancelAiTasks() {
      return safeApiCall('cancel_ai_tasks', [], {
        status: 'success',
        message: '已取消 AI 任务（Mock）',
      })
    },

    /**
     * 清除 AI 取消标志
     */
    async clearAiCancel() {
      return safeApiCall('clear_ai_cancel', [], null)
    },

    /**
     * 执行启动检查
     */
    async performStartupCheck() {
      return safeApiCall('perform_startup_check', [], null)
    },

    // ==================== 窗口控制 API ====================

    /**
     * 设置窗口置顶
     */
    async setWindowOnTop(onTop) {
      return safeApiCall('set_window_on_top', [onTop], null)
    },

    /**
     * 隐藏窗口
     */
    async hideWindow() {
      return safeApiCall('hide_window', [], null)
    },

    /**
     * 最小化窗口
     */
    async minimizeApp() {
      return safeApiCall('minimize_app', [], null)
    },

    /**
     * 关闭应用
     */
    async closeApp() {
      return safeApiCall('close_app', [], null)
    },

    /**
     * 强制退出
     */
    async forceQuit() {
      return safeApiCall('force_quit', [], null)
    },

    // ==================== 浏览器相关 API ====================

    /**
     * 打开链接（优先使用系统链接）
     */
    async openLink(url) {
      if (!isPywebviewAvailable()) {
        // 浏览器环境直接打开新标签
        window.open(url, '_blank')
        return
      }

      try {
        // 优先使用 open_system_link（macOS）
        if (window.pywebview.api.open_system_link) {
          await window.pywebview.api.open_system_link(url)
        } else {
          await window.pywebview.api.open_browser(url)
        }
      } catch (error) {
        console.error('[usePyWebview] openLink failed:', error)
        // 降级：尝试直接打开
        window.open(url, '_blank')
      }
    },

    /**
     * 打开浏览器
     */
    async openBrowser(url) {
      return safeApiCall('open_browser', [url], null)
    },

    /**
     * 在应用内浏览器打开
     */
    async openInBrowser(url) {
      return safeApiCall('open_in_browser', [url], null)
    },

    // ==================== AI 相关 API ====================

    /**
     * 获取本地 AI 图标
     */
    async getLocalAiIcon(modelName) {
      return safeApiCall('get_local_ai_icon', [modelName], null)
    },

    // ==================== 回调注册/注销 ====================

    /**
     * 注册回调函数（供后端调用）
     */
    registerCallback(name, callback) {
      if (!isPywebviewAvailable()) {
        console.warn(`[usePyWebview] registerCallback(${name}) called in browser environment`)
        return
      }

      try {
        window[name] = callback
        console.log(`[usePyWebview] Callback registered: ${name}`)
      } catch (error) {
        console.error(`[usePyWebview] registerCallback(${name}) failed:`, error)
      }
    },

    /**
     * 注销回调函数
     */
    unregisterCallback(name) {
      if (!isPywebviewAvailable()) {
        return
      }

      try {
        delete window[name]
        console.log(`[usePyWebview] Callback unregistered: ${name}`)
      } catch (error) {
        console.error(`[usePyWebview] unregisterCallback(${name}) failed:`, error)
      }
    },

    /**
     * 批量注册回调函数
     */
    registerCallbacks(callbacks) {
      Object.entries(callbacks).forEach(([name, callback]) => {
        this.registerCallback(name, callback)
      })
    },

    /**
     * 批量注销回调函数
     */
    unregisterCallbacks(names) {
      names.forEach(name => {
        this.unregisterCallback(name)
      })
    },

    // ==================== 回调名称常量 ====================

    /**
     * 所有回调名称（供后端调用）
     */
    CALLBACK_NAMES: {
      UPDATE_PY_PROGRESS: 'updatePyProgress',
      UPDATE_SPIDER_PROGRESS: 'updateSpiderProgress',
      ON_START_FETCHING: 'onStartFetching',
      SHOW_AUTO_FETCH_NOTICE: 'showAutoFetchNotice',
      TRIGGER_AUTO_FETCH_COOLDOWN: 'triggerAutoFetchCooldown',
      ON_SPIDER_COMPLETE: 'onSpiderComplete',
      SHOW_ARTICLE_DETAIL: 'showArticleDetailFromBackend',
      SILENT_UPDATE_ARTICLE: 'silentUpdateArticle',
      OPEN_ARTICLE_DETAIL: 'openArticleDetail',
      UPDATE_API_BALANCE_STATUS: 'updateApiBalanceStatus',
    },
  }
}

// 导出单例
export const pywebviewApi = usePyWebview()

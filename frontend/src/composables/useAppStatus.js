/**
 * 全局状态、进度条与后端回调模块 Composable
 * 负责管理加载状态、进度条、更新检查、冷却时间、后端回调桥接等功能
 */
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { usePyWebview } from './usePyWebview'

const pywebview = usePyWebview()

// 冷却时间常量
const COOLDOWN_SECONDS = 120

/**
 * 全局应用状态模块
 * @param {Object} options - 配置选项
 * @param {import('vue').Ref} options.config - 配置对象引用
 * @param {import('vue').Ref} options.statusMsg - 状态消息引用
 * @param {import('vue').Ref} options.articles - 文章列表引用
 * @param {import('vue').Ref} options.page - 页码引用
 * @param {import('vue').Ref} options.noMoreData - 无更多数据标志引用
 * @param {import('vue').Ref} options.isReadOnlyMode - 只读模式引用
 * @param {import('vue').Ref} options.readOnlyReason - 只读原因引用
 * @param {import('vue').Ref} options.activeSource - 当前来源引用
 * @param {import('vue').Ref} options.sources - 来源列表引用
 * @param {import('vue').Ref} options.aiBrandName - AI 品牌名引用
 */
export function useAppStatus(options = {}) {
  const {
    config,
    statusMsg,
    articles,
    page,
    noMoreData,
    isReadOnlyMode,
    readOnlyReason,
    activeSource,
    sources,
    aiBrandName,
    onLoadComplete,
  } = options

  // ==================== 状态定义 ====================

  // 加载状态
  const isLoading = ref(false)
  const isCapturing = ref(false)

  // 更新阶段：'idle', 'spider', 'ai'
  const updatePhase = ref('idle')

  // 进度数据
  const spiderProgress = ref({ total: 0, completed: 0, detail: '' })
  const aiProgress = ref({ total: 0, completed: 0, detail: '' })
  const hasPendingAiTasks = ref(false)
  const totalTasks = ref(0)
  const completedTasks = ref(0)

  // 冷却时间
  const updateCooldown = ref(0)
  const isUpdateDisabled = ref(false)
  let cooldownTimer = null

  // 取消标志
  const isCancelledFlag = ref(false)

  // ==================== 计算属性 ====================

  /**
   * 进度百分比
   */
  const progressPercent = computed(() =>
    totalTasks.value === 0
      ? 0
      : Math.round((completedTasks.value / totalTasks.value) * 100)
  )

  /**
   * 进度标题
   */
  const progressTitle = computed(() => {
    if (updatePhase.value === 'spider') return '检索进度'
    if (updatePhase.value === 'ai') return 'AI 总结进度'
    return ''
  })

  // ==================== 方法定义 ====================

  /**
   * 播放按钮动画
   */
  const animateButtonClick = (btnElement) => {
    if (!btnElement) return
    btnElement.classList.add('btn-click-scale')
    setTimeout(() => btnElement.classList.remove('btn-click-scale'), 150)
  }

  /**
   * 按钮脉冲动画
   */
  const pulseButton = (btnElement) => {
    if (!btnElement) return
    btnElement.classList.add('btn-pulse')
    setTimeout(() => btnElement.classList.remove('btn-pulse'), 500)
  }

  /**
   * 启动冷却倒计时
   */
  const startCooldown = (remainingSeconds) => {
    if (!remainingSeconds || remainingSeconds <= 0) {
      if (cooldownTimer) clearInterval(cooldownTimer)
      isUpdateDisabled.value = false
      updateCooldown.value = 0
      return
    }

    isUpdateDisabled.value = true
    updateCooldown.value = remainingSeconds

    if (cooldownTimer) clearInterval(cooldownTimer)

    cooldownTimer = setInterval(() => {
      updateCooldown.value--
      if (updateCooldown.value <= 0) {
        clearInterval(cooldownTimer)
        cooldownTimer = null
        isUpdateDisabled.value = false
        updateCooldown.value = 0
        if (typeof localStorage !== 'undefined') {
          localStorage.removeItem('lastManualUpdateTime')
        }
      }
    }, 1000)

    // 同步存储时间戳
    if (typeof localStorage !== 'undefined') {
      const simulatedStartTime = Date.now() - (COOLDOWN_SECONDS - remainingSeconds) * 1000
      localStorage.setItem('lastManualUpdateTime', simulatedStartTime.toString())
    }
  }

  /**
   * 检查更新
   */
  const checkUpdates = async () => {
    if (isUpdateDisabled.value) return

    isCancelledFlag.value = false

    try {
      await pywebview.clearAiCancel()
    } catch (e) {
      // 忽略错误
    }

    // 重置进度
    updatePhase.value = 'spider'
    spiderProgress.value = { total: 0, completed: 0, detail: '正在启动...' }
    aiProgress.value = { total: 0, completed: 0, detail: '' }
    hasPendingAiTasks.value = false
    isLoading.value = true

    const btn = document.querySelector('.btn-check')
    animateButtonClick(btn)
    pulseButton(btn)

    if (statusMsg) statusMsg.value = ''

    let cooldownRemaining = 120

    try {
      const res = await pywebview.checkUpdates(true)

      // 检查是否已取消
      if (isCancelledFlag.value) {
        isLoading.value = false
        return
      }

      if (res.status === 'success') {
        // 云端恢复后解除只读模式
        if (isReadOnlyMode?.value) {
          isReadOnlyMode.value = false
          if (readOnlyReason) readOnlyReason.value = ''
        }

        if (articles) articles.value = res.data || []
        if (page) page.value = 1
        if (noMoreData) noMoreData.value = false

        // 刷新来源列表
        try {
          const sourcesRes = await pywebview.getAllSources()
          if (sourcesRes.status === 'success' && sourcesRes.data && sources) {
            sources.value = ['全部', ...sourcesRes.data]
          }
        } catch (e) {
          console.warn('刷新来源列表失败:', e)
        }

        const count = res.submitted_count || res.submittedCount || 0
        if (count > 0) {
          const brandName = aiBrandName?.value || 'AI'
          if (statusMsg) {
            statusMsg.value = `找到 ${count} 篇新内容，正在由 ${brandName} 总结...`
            setTimeout(() => {
              if (statusMsg.value?.includes('正在由')) statusMsg.value = ''
            }, 3000)
          }
          hasPendingAiTasks.value = true
          updatePhase.value = 'ai'
          aiProgress.value = { total: 0, completed: 0, detail: '等待 AI 处理...' }
        } else {
          if (statusMsg) {
            statusMsg.value = '当前已是最新，无新内容'
            setTimeout(() => {
              if (statusMsg) statusMsg.value = ''
            }, 4000)
          }
          isLoading.value = false
        }
        cooldownRemaining =
          res.cooldown_remaining !== undefined
            ? res.cooldown_remaining
            : res.cooldownRemaining || 120
      } else if (res.status === 'cooldown') {
        if (statusMsg) {
          statusMsg.value = res.message || '冷却中'
          setTimeout(() => {
            if (statusMsg) statusMsg.value = ''
          }, 3000)
        }
        cooldownRemaining = res.remaining || 120
        isLoading.value = false
      } else if (res.status === 'read_only') {
        if (statusMsg) {
          statusMsg.value = '⚠️ 服务已暂停，当前为只读模式'
          setTimeout(() => {
            if (statusMsg) statusMsg.value = ''
          }, 5000)
        }
        cooldownRemaining = 0
        isLoading.value = false
      } else {
        if (statusMsg) {
          statusMsg.value = res.message || '更新失败'
          setTimeout(() => {
            if (statusMsg) statusMsg.value = ''
          }, 3000)
        }
        cooldownRemaining = res.cooldown_remaining || res.cooldownRemaining || 60
        isLoading.value = false
      }
    } catch (e) {
      if (statusMsg) {
        statusMsg.value = '连接异常或超时'
        setTimeout(() => {
          if (statusMsg) statusMsg.value = ''
        }, 3000)
      }
      cooldownRemaining = 60
      isLoading.value = false
    } finally {
      pulseButton(btn)
      startCooldown(cooldownRemaining)
    }
  }

  /**
   * 取消 AI 任务
   */
  const cancelAITasks = async () => {
    isCancelledFlag.value = true
    console.log('前端按钮已点击，准备呼叫后端 cancel_ai_tasks')

    try {
      const res = await pywebview.cancelAiTasks()
      console.log('后端返回结果:', res)

      if (res.status === 'success') {
        const brandName = aiBrandName?.value || 'AI'
        if (statusMsg) {
          statusMsg.value = `已终止 ${brandName} 总结`
          setTimeout(() => {
            if (statusMsg) statusMsg.value = ''
          }, 3000)
        }

        // 重置状态
        isLoading.value = false
        updatePhase.value = 'idle'
        aiProgress.value = { total: 0, completed: 0, detail: '' }
        spiderProgress.value = { total: 0, completed: 0, detail: '' }
        hasPendingAiTasks.value = false
      } else {
        if (statusMsg) statusMsg.value = '终止失败: ' + res.message
      }
    } catch (e) {
      if (statusMsg) statusMsg.value = '终止失败，请重试'
      console.error('取消AI任务失败:', e)
    }
  }

  /**
   * 统一按钮点击处理
   */
  const handleButtonClick = () => {
    if (isLoading.value) {
      cancelAITasks()
      return
    }

    if (isReadOnlyMode?.value) {
      if (statusMsg) {
        statusMsg.value = '服务已暂停，当前为只读模式，感谢您的使用！'
        setTimeout(() => {
          if (statusMsg) statusMsg.value = ''
        }, 3000)
      }
      return
    }

    checkUpdates()
  }

  // ==================== 后端回调桥接 ====================

  /**
   * 注册所有后端回调
   */
  const registerBackendCallbacks = () => {
    // AI 进度回调
    window.updatePyProgress = (completed, total, currentTitle) => {
      if (isCancelledFlag.value) return

      if (total === 0 && completed === 0) {
        isLoading.value = false
        hasPendingAiTasks.value = false
        return
      }

      if (completed >= total && total > 0) {
        isLoading.value = false
        hasPendingAiTasks.value = false
        pywebview.clearAiCancel().catch(() => {})
      }

      updatePhase.value = 'ai'
      const brandName = aiBrandName?.value || 'AI'
      aiProgress.value = {
        total,
        completed,
        detail: currentTitle ? `${brandName}: ${currentTitle}` : brandName,
      }
    }

    // 爬虫进度回调
    window.updateSpiderProgress = (current, total, sourceName) => {
      if (isCancelledFlag.value) return

      if (total === 0) {
        isLoading.value = false
        return
      }

      if (!isLoading.value) {
        isLoading.value = true
      }

      updatePhase.value = 'spider'
      spiderProgress.value = {
        total,
        completed: current,
        detail:
          sourceName === '正在启动...'
            ? `正在启动 (共 ${total} 个)...`
            : `正在检索： ${sourceName}`,
      }
    }

    // 开始执行爬虫
    window.onStartFetching = () => {
      console.log('🔔 收到开始执行通知')
      isLoading.value = true
      updatePhase.value = 'spider'
      spiderProgress.value = { total: 0, completed: 0, detail: '正在启动...' }
      aiProgress.value = { total: 0, completed: 0, detail: '' }
      startCooldown(120)
    }

    // 后台自动执行提示
    window.showAutoFetchNotice = () => {
      console.log('🔔 收到后台自动执行通知')
      if (statusMsg) {
        statusMsg.value = '已自动执行开机后台巡检，120s内无需重复刷新'
        setTimeout(() => {
          if (statusMsg.value?.includes('自动执行')) statusMsg.value = ''
        }, 5000)
      }
    }

    // 后台完成通知
    window.triggerAutoFetchCooldown = () => {
      console.log('🔔 收到后台执行完成确认')
      startCooldown(120)
    }

    // 爬虫阶段完成
    window.onSpiderComplete = (hasNewArticles) => {
      console.log('🔔 爬虫阶段完成，是否有新文章:', hasNewArticles)

      if (hasNewArticles) {
        updatePhase.value = 'ai'
        aiProgress.value = { total: 0, completed: 0, detail: '等待 AI 处理...' }
        hasPendingAiTasks.value = true
      } else {
        isLoading.value = false
        updatePhase.value = 'idle'
        spiderProgress.value = { total: 0, completed: 0, detail: '' }
        aiProgress.value = { total: 0, completed: 0, detail: '' }
        hasPendingAiTasks.value = false
        if (statusMsg) {
          statusMsg.value = '当前已是最新，无新内容'
          setTimeout(() => {
            if (statusMsg) statusMsg.value = ''
          }, 4000)
        }
      }
    }
  }

  /**
   * 注销所有后端回调
   */
  const unregisterBackendCallbacks = () => {
    delete window.updatePyProgress
    delete window.updateSpiderProgress
    delete window.onStartFetching
    delete window.showAutoFetchNotice
    delete window.triggerAutoFetchCooldown
    delete window.onSpiderComplete
  }

  /**
   * 初始化冷却恢复
   */
  const initCooldownRecovery = () => {
    if (typeof localStorage === 'undefined') return

    const lastTime = localStorage.getItem('lastManualUpdateTime')
    if (lastTime) {
      const elapsedSeconds = Math.floor((Date.now() - parseInt(lastTime)) / 1000)
      if (elapsedSeconds < COOLDOWN_SECONDS) {
        startCooldown(COOLDOWN_SECONDS - elapsedSeconds)
        console.log(`🔄 恢复冷却倒计时: 剩余 ${COOLDOWN_SECONDS - elapsedSeconds} 秒`)
      } else {
        localStorage.removeItem('lastManualUpdateTime')
      }
    }
  }

  /**
   * 从后端同步冷却时间
   */
  const syncCooldownFromBackend = async () => {
    try {
      // 检查待处理冷却
      if (window._pendingCooldown) {
        console.log('🔄 处理待处理的冷却通知')
        window._pendingCooldown = false
        startCooldown(120)
        return
      }

      const cooldownRes = await pywebview.getUpdateCooldown()
      if (cooldownRes && cooldownRes.status === 'cooling' && cooldownRes.remaining > 0) {
        console.log(`🔄 从后端恢复真实的冷却倒计时: 剩余 ${cooldownRes.remaining} 秒`)
        startCooldown(cooldownRes.remaining)
      }
    } catch (e) {
      console.warn('同步冷却时间失败:', e)
    }
  }

  /**
   * 执行启动检查
   */
  const performStartupCheck = async () => {
    try {
      const checkResult = await pywebview.performStartupCheck()

      if (checkResult.status === 'locked' || checkResult.status === 'network_error') {
        const overlay = document.getElementById('kill-switch-overlay')
        const reasonText = document.getElementById('kill-switch-reason')

        if (overlay) overlay.style.display = 'flex'
        if (reasonText) reasonText.innerText = checkResult.reason

        const mainContent = document.getElementById('app')
        if (mainContent) {
          mainContent.innerHTML = ''
        }
        return false
      }

      if (checkResult.mode === 'read_only') {
        if (isReadOnlyMode) isReadOnlyMode.value = true
        if (readOnlyReason) readOnlyReason.value = checkResult.reason || '服务已暂停'
        console.log('⚠️ 启动时检测到只读模式:', checkResult.reason)
      }

      return true
    } catch (e) {
      console.warn('启动安全检查失败，采取默认放行策略:', e)
      return true
    }
  }

  /**
   * 全局快捷键劫持
   */
  const setupGlobalShortcuts = () => {
    const handleKeydown = (e) => {
      // Cmd+Q / Ctrl+Q 强制退出
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'q') {
        e.preventDefault()
        pywebview.forceQuit()
      }
    }

    window.addEventListener('keydown', handleKeydown)
    return () => window.removeEventListener('keydown', handleKeydown)
  }

  /**
   * 设置链接点击拦截器
   */
  const setupLinkClickInterceptor = () => {
    const handleClick = (e) => {
      const link = e.target.closest('.markdown-body a')
      if (link) {
        e.preventDefault()
        let url = link.getAttribute('href')

        if (url && url.includes('@') && !url.startsWith('mailto:') && !url.startsWith('http')) {
          url = 'mailto:' + url
        }

        if (url) {
          pywebview.openLink(url)
        }
      }
    }

    document.addEventListener('click', handleClick)
    return () => document.removeEventListener('click', handleClick)
  }

  // ==================== 生命周期 ====================

  /**
   * 初始化应用状态
   */
  const initialize = async () => {
    // 注册后端回调
    registerBackendCallbacks()

    // 初始化冷却恢复
    initCooldownRecovery()

    // 设置全局快捷键
    const cleanupShortcuts = setupGlobalShortcuts()

    // 设置链接拦截
    const cleanupLinkInterceptor = setupLinkClickInterceptor()

    // 监听 pywebview ready
    window.addEventListener('pywebviewready', async () => {
      // 启动安全检查
      const canContinue = await performStartupCheck()
      if (!canContinue) return

      // 从后端同步冷却时间
      setTimeout(syncCooldownFromBackend, 500)

      // 调用完成回调
      if (onLoadComplete) {
        onLoadComplete()
      }
    })

    return () => {
      cleanupShortcuts()
      cleanupLinkInterceptor()
      unregisterBackendCallbacks()
      if (cooldownTimer) clearInterval(cooldownTimer)
    }
  }

  // ==================== 返回 ====================

  return {
    // 状态
    isLoading,
    isCapturing,
    updatePhase,
    spiderProgress,
    aiProgress,
    hasPendingAiTasks,
    totalTasks,
    completedTasks,
    updateCooldown,
    isUpdateDisabled,

    // 计算属性
    progressPercent,
    progressTitle,

    // 方法
    startCooldown,
    checkUpdates,
    cancelAITasks,
    handleButtonClick,
    animateButtonClick,
    pulseButton,
    registerBackendCallbacks,
    unregisterBackendCallbacks,
    initCooldownRecovery,
    syncCooldownFromBackend,
    performStartupCheck,
    setupGlobalShortcuts,
    setupLinkClickInterceptor,
    initialize,
  }
}

/**
 * 回调名称常量
 */
export const CALLBACK_NAMES = {
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
}

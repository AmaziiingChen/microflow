/**
 * 配置与设置模块 Composable
 * 负责管理应用配置、设置面板、字体、来源订阅等功能
 */
import { ref, computed, watch } from 'vue'
import { usePyWebview } from './usePyWebview'

const pywebview = usePyWebview()

// 默认提示词常量
export const DEFAULT_PROMPT = `
# Role: 高校公文首席分析师

## Profile
你是一位资深的高校公文分析专家。你的任务是仔细阅读原文，精准识别公文类型，提取核心要素，并采用最适合该公文逻辑的结构进行高度概括的总结。

## 严格执行规则 (Rules)

### 1. 实体标记规则 (XML标签包裹)
请扫视全文（包括标题、正文及落款），对以下三类核心实体进行无遗漏的标签包裹：
- **时间与期限**：遇到具体时间、日期、截止期限，必须用 <date> 包裹。例：<date>3月15日</date>。
- **物理空间**：遇到任何实体建筑、楼层、教室编号、会议室、集会地点等，必须用 <loc> 包裹。例：<loc>行政楼804室</loc>。
- **联系方式**：遇到电话号码、手机号、邮箱、微信号等，必须用 <contact> 包裹。例：<contact>138xxxxxxx</contact>。

### 2. 视觉与排版铁律 (Markdown规范)
- **禁用代码块**：输出内容严禁使用 \\\`\\\`\\\` 包裹为代码块。
- **标题层级**：正文一级板块必须使用 \\\`### \\\` (三级标题) 开头，按需可使用 \\\`#### \\\` 作为子板块。
- **列表化表达**：凡涉及多个并列项（如要求、步骤、材料清单等），一律使用无序列表 \\\`- \\\` 或有序列表 \\\`1. \\\`。
- **高亮强调**：对关键信息（金额、实体标签等）需进行加粗强调。**注意**：为了确保 Markdown 渲染成功，加粗符号 \\\`**\\\` 与其前后的非加粗文字之间**必须保留一个半角空格**（例如：地点设在 ** <loc>大礼堂</loc> ** 举行）。
- **信息缺失处理**：若原文未提及时间、地点或截止日期，请忽略该字段或备注"详见原文"，**绝对禁止编造或推理**信息。不必列举发文单位和发文日期。

### 3. 链接强制保留规则：
- 严禁忽略原文中的关键外部链接（如：报名链接、全文链接、公示名单链接、论文访问地址等）。
- 必须在总结的相关段落末尾，以 [点击访问](URL地址) 的 Markdown 格式完整保留。
- 如果链接过长，也必须全文保留，不得截断。

## 输出格式规范 (Output Format)

【标签1】【标签2】【标签3】
（注意，对于第一个标签，强制使用2个中文汉字进行总结，后续的不做要求）

### [自定义板块标题1]
- 内容详情...
### [自定义板块标题2]
- 内容详情...

**标签要求**：
1. 必须放在第一行，最多4个，每个标签用 \\\`【】\\\` 包裹。
2. 内容必须是精炼的关键词，严禁使用完整句子。
3. 【标签1】必须是公文性质（如：通知、公告、申请、报告、方案、总结等），需从文中提取明确线索，不可臆断。
4. 后续标签应涵盖：[受众群体]、[核心动作] 等符合信息传递核心的关键词。

**正文要求**：
1. 不要套用固定模板，请根据公文的内在逻辑自由创建板块（例如："报名详情"、"评审流程"、"注意事项"等），目标是让读者一眼抓住核心。
2. 次要说明、背景信息、附件链接等细节可放在最后的板块中。

## Input:
{raw_text}
`

// 标签颜色
const tagColors = ['blue', 'green', 'purple', 'rose', 'amber']

/**
 * 获取标签颜色
 */
export const getTagColor = (index) => tagColors[index % tagColors.length]

/**
 * 配置与设置模块
 */
export function useSettings() {
  // ==================== 状态定义 ====================

  // 配置对象
  const config = ref({
    baseUrl: '',
    apiKey: '',
    modelName: '',
    prompt: '',
    autoStart: false,
    muteMode: false,
    trackMode: 'continuous',
    fontFamily: 'sans-serif',
    customFontPath: '',
    customFontName: '',
    subscribedSources: [],
    pollingInterval: 60,
    isPinned: false,
    readNoticeTime: '',
  })

  // 设置面板状态
  const isSettingsOpen = ref(false)
  const showApiKey = ref(false)
  const settingsMsg = ref('')
  const isTesting = ref(false)
  const isRegenerating = ref(false)

  // 视图历史
  const previousView = ref('list')

  // AI 图标相关
  const localAiIconSvg = ref('')
  const iconLoadError = ref(false)

  // AI 品牌标题
  const aiIntelligenceTitle = computed(() => {
    const model = config.value.modelName || 'AI'
    let brand = model.split('-')[0].toUpperCase()
    if (brand.length < 2) brand = model.toUpperCase()
    return `${brand} INTELLIGENCE`
  })

  // AI 品牌名称（友好显示）
  const aiBrandName = computed(() => {
    const model = config.value.modelName || 'AI'
    let brand = model.split('-')[0].toLowerCase()
    const brandMap = {
      deepseek: 'DeepSeek',
      glm: 'GLM',
      qwen: '通义千问',
      mimo: 'MiMo',
      gpt: 'GPT',
      claude: 'Claude',
      gemini: 'Gemini',
    }
    return brandMap[brand] || brand.charAt(0).toUpperCase() + brand.slice(1)
  })

  // 置顶动画状态
  const pinAnimating = ref(false)

  // 只读模式
  const isReadOnlyMode = ref(false)
  const readOnlyReason = ref('')

  // 所有可用来源（固定顺序，与后端 SPIDER_REGISTRY 一致）
  const allAvailableSources = ref([
    '公文通',
    '中德智能制造学院',
    '人工智能学院',
    '新材料与新能源学院',
    '城市交通与物流学院',
    '健康与环境工程学院',
    '工程物理学院',
    '药学院',
    '集成电路与光电芯片学院',
    '未来技术学院',
    '创意设计学院',
    '商学院',
    '外国语学院',
  ])

  // 系统公告
  const systemNotice = ref({
    id: 'sys_notice_1',
    title: '欢迎使用 MicroFlow (微流)',
    publish_time: new Date().toLocaleDateString('zh-CN', {
      year: 'numeric',
      month: 'long',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    }),
    department: '系统公告',
    source_name: '系统通知',
    category: 'v1.0.0',
    tags: ['新手指南', '版本 1.0.0'],
    url: 'https://github.com/AmaziiingChen/microflow',
    summary:
      '初次见面！为了让 AI 能够为您智能总结公文，请点击本卡片进入设置界面，配置您的 API 密钥。这里未来也会是接收最新版本更新和系统通知的地方。',
    content:
      '亲爱的同学/老师，欢迎使用 MicroFlow！<br><br>为了让 AI 能够为您智能总结长篇公文，您需要先配置 AI 的 API 密钥。<br><br>👉 **配置方法**：请点击本界面**左上角的「设置/齿轮」图标**，在弹出的面板中填入您的 API 信息。<br><br>配置完成后，您就可以体验极速的公文摘要功能了。本卡片不仅是新手指南，未来也会在这里向您推送最新的版本更新说明。',
    is_announcement: true,
    version: '1.0.0',
  })

  // 云端版本号
  const remoteVersion = ref('')

  // 欠费卡片相关
  const balanceWarning = ref({
    id: 'balance_warning',
    title: 'API 余额不足',
    content:
      '您的 API 账户余额不足，AI 总结功能已暂停。请充值或更换密钥后点击下方按钮恢复。',
    button_text: '我已充值',
    is_announcement: true,
    version: 'balance_v1',
  })
  const balanceWarningVisible = ref(false)
  const isBalanceWarningDismissed = ref(
    typeof localStorage !== 'undefined' &&
      localStorage.getItem('balance_warning_dismissed') === 'true'
  )

  // ==================== 方法定义 ====================

  /**
   * 显示系统提示
   */
  const showSystemToast = (msg, type = 'info') => {
    // 这里需要由上层注入 statusMsg
    console.log(`[Toast][${type}] ${msg}`)
  }

  /**
   * 开启设置面板
   */
  const openSettings = async () => {
    if (isSettingsOpen.value) {
      closeSettingsWithAutoSave()
      return
    }

    isSettingsOpen.value = true
    settingsMsg.value = ''

    // 自动滚动到顶部
    setTimeout(() => {
      const listView = document.querySelector('.list-view')
      if (listView) listView.scrollTo({ top: 0, behavior: 'smooth' })
    }, 50)

    // 加载配置
    const res = await pywebview.loadConfig()
    if (res.status === 'success') {
      config.value = { ...config.value, ...res }
    }
  }

  /**
   * 关闭设置面板
   */
  const closeSettings = () => {
    isSettingsOpen.value = false
  }

  /**
   * 带自动保存的关闭
   */
  const closeSettingsWithAutoSave = async () => {
    try {
      const res = await pywebview.saveConfig(JSON.parse(JSON.stringify(config.value)))
      if (res.status === 'success') {
        settingsMsg.value = '配置已保存并应用'
      } else {
        settingsMsg.value = '保存失败: ' + res.message
      }
    } catch (e) {
      settingsMsg.value = '保存发生错误'
    }
    isSettingsOpen.value = false
  }

  /**
   * 保存设置
   */
  const saveSettings = async () => {
    settingsMsg.value = '正在保存...'
    const res = await pywebview.saveConfig(JSON.parse(JSON.stringify(config.value)))
    if (res.status === 'success') {
      settingsMsg.value = '配置保存成功！'
      setTimeout(() => (settingsMsg.value = ''), 3000)
    } else {
      settingsMsg.value = '保存失败: ' + res.message
    }
  }

  /**
   * 测试 AI 连接
   */
  const testConnection = async () => {
    isTesting.value = true
    settingsMsg.value = '正在连接 AI 服务器...'

    const res = await pywebview.testAiConnection(
      config.value.baseUrl,
      config.value.apiKey,
      config.value.modelName
    )
    isTesting.value = false

    if (res.status === 'success') {
      settingsMsg.value = '连接成功！服务器响应正常。'
    } else {
      settingsMsg.value = '连接失败: ' + res.message
    }
  }

  /**
   * 恢复默认提示词
   */
  const resetPrompt = () => {
    config.value.prompt = DEFAULT_PROMPT
    settingsMsg.value = '提示词已恢复默认，点击保存生效'
  }

  /**
   * 导入自定义字体
   */
  const importCustomFont = async () => {
    const res = await pywebview.importCustomFont()
    if (res.status === 'success') {
      config.value.customFontPath = res.font_path
      config.value.customFontName = res.font_name
      config.value.fontFamily = 'custom'
      showSystemToast(`✅ 成功导入字体: ${res.font_name}`, 'success')
    } else if (res.status === 'error') {
      showSystemToast(`❌ 导入失败: ${res.message}`, 'error')
    }
  }

  /**
   * 切换置顶状态
   */
  const togglePin = async () => {
    pinAnimating.value = true
    setTimeout(() => {
      pinAnimating.value = false
    }, 400)

    config.value.isPinned = !config.value.isPinned
    await pywebview.setWindowOnTop(config.value.isPinned)
    await pywebview.saveConfig(JSON.parse(JSON.stringify(config.value)))
  }

  /**
   * 处理图标加载错误
   */
  const handleIconError = () => {
    iconLoadError.value = true
  }

  /**
   * 全选来源
   */
  const selectAllSources = () => {
    config.value.subscribedSources = [...allAvailableSources.value]
  }

  /**
   * 取消全选来源
   */
  const deselectAllSources = () => {
    config.value.subscribedSources = []
  }

  /**
   * 返回上一页
   */
  const backToPrevious = () => {
    // 需要由上层注入 currentView
    console.log('[useSettings] backToPrevious called')
  }

  /**
   * 关闭应用窗口
   */
  const closeAppWindow = () => {
    pywebview.hideWindow()
  }

  /**
   * 云端配置拉取
   */
  const fetchSystemConfig = async () => {
    try {
      const res = await pywebview.getVersionInfo()
      if (res.status !== 'success') {
        console.warn('⚠️ 后端获取版本信息失败，使用本地保底公告')
        return
      }

      if (res.announcement) {
        const newPublishTime = res.announcement.publish_time || ''
        const localReadTime = config.value.readNoticeTime || ''

        if (newPublishTime && localReadTime && newPublishTime > localReadTime) {
          console.log('🔔 检测到新公告，清除已读状态')
          config.value.readNoticeTime = ''
          try {
            await pywebview.saveConfig(JSON.parse(JSON.stringify(config.value)))
          } catch (e) {
            console.warn('清除公告已读状态失败:', e)
          }
        }

        systemNotice.value = {
          id: res.announcement.id || 'sys_notice_1',
          title: res.announcement.title || systemNotice.value.title,
          publish_time:
            res.announcement.publish_time || systemNotice.value.publish_time,
          department: res.announcement.department || '系统公告',
          source_name: res.announcement.source_name || '系统通知',
          category:
            res.announcement.category ||
            res.announcement.version ||
            systemNotice.value.category,
          tags: res.announcement.tags || systemNotice.value.tags,
          url: res.announcement.url || systemNotice.value.url,
          summary: res.announcement.summary || systemNotice.value.summary,
          content: res.announcement.content || systemNotice.value.content,
          is_announcement: true,
          version: res.announcement.version || systemNotice.value.version,
        }
      }

      if (res.version) {
        remoteVersion.value = res.version
      }
    } catch (error) {
      console.warn('云端配置拉取失败:', error.message)
    }
  }

  /**
   * 检查 API 余额
   */
  const checkApiBalance = async () => {
    try {
      const res = await pywebview.getApiBalanceStatus()
      if (res.status === 'success') {
        if (!res.balanceOk) {
          balanceWarningVisible.value = true
          isBalanceWarningDismissed.value = false
          if (typeof localStorage !== 'undefined') {
            localStorage.removeItem('balance_warning_dismissed')
          }
        } else {
          balanceWarningVisible.value = false
          isBalanceWarningDismissed.value = false
          if (typeof localStorage !== 'undefined') {
            localStorage.removeItem('balance_warning_dismissed')
          }
        }
      }
    } catch (e) {
      console.warn('获取余额状态失败:', e)
    }
  }

  /**
   * 清除欠费状态
   */
  const clearBalanceWarning = async (statusMsg) => {
    try {
      await pywebview.clearApiBalanceStatus()
      balanceWarningVisible.value = false
      isBalanceWarningDismissed.value = false
      if (typeof localStorage !== 'undefined') {
        localStorage.removeItem('balance_warning_dismissed')
      }
      if (statusMsg) {
        statusMsg.value = '已清除欠费状态，AI 功能恢复'
        setTimeout(() => (statusMsg.value = ''), 3000)
      }
    } catch (e) {
      console.warn('清除欠费状态失败:', e)
    }
  }

  /**
   * 关闭欠费卡片
   */
  const dismissBalanceWarning = () => {
    isBalanceWarningDismissed.value = true
    balanceWarningVisible.value = false
    if (typeof localStorage !== 'undefined') {
      localStorage.setItem('balance_warning_dismissed', 'true')
    }
  }

  // ==================== 监听器 ====================

  /**
   * 监听字体配置变化
   */
  watch(
    () => [config.value.fontFamily, config.value.customFontPath],
    ([newFont, customPath]) => {
      const oldStyle = document.getElementById('tongwen-custom-font')
      if (oldStyle) oldStyle.remove()

      document.body.classList.remove('serif-mode', 'custom-font-mode')

      if (newFont === 'serif') {
        document.body.classList.add('serif-mode')
      } else if (newFont === 'custom' && customPath) {
        const absoluteFontUrl = new URL(customPath, window.location.href).href
        const style = document.createElement('style')
        style.id = 'tongwen-custom-font'
        style.innerHTML = `
          @font-face {
            font-family: 'UserCustomFont';
            src: url('${absoluteFontUrl}') format('${
              customPath.endsWith('woff2')
                ? 'woff2'
                : customPath.endsWith('woff')
                  ? 'woff'
                  : customPath.endsWith('otf')
                    ? 'opentype'
                    : 'truetype'
            }');
          }
          .custom-font-mode,
          .custom-font-mode .markdown-body, .custom-font-mode .list-card-title,
          .custom-font-mode .detail-title, .custom-font-mode .form-input,
          .custom-font-mode .search-input, .custom-font-mode .btn-check,
          .custom-font-mode button, .custom-font-mode input {
            font-family: 'UserCustomFont', "Helvetica Neue", Helvetica, "Segoe UI", Arial, sans-serif !important;
          }
        `
        document.head.appendChild(style)
        document.body.classList.add('custom-font-mode')
      }
    },
    { immediate: true, deep: true }
  )

  /**
   * 监听模型名称变化，加载 AI 图标
   */
  watch(
    () => config.value.modelName,
    async (newModel) => {
      if (!newModel) return
      const res = await pywebview.getLocalAiIcon(newModel)
      if (res && res.svg_raw) {
        localAiIconSvg.value = res.svg_raw
        iconLoadError.value = false
      }
    },
    { immediate: true }
  )

  // ==================== 返回 ====================

  return {
    // 状态
    config,
    isSettingsOpen,
    showApiKey,
    settingsMsg,
    isTesting,
    isRegenerating,
    previousView,
    localAiIconSvg,
    aiIntelligenceTitle,
    aiBrandName,
    iconLoadError,
    pinAnimating,
    isReadOnlyMode,
    readOnlyReason,
    allAvailableSources,
    systemNotice,
    remoteVersion,
    balanceWarning,
    balanceWarningVisible,
    isBalanceWarningDismissed,

    // 方法
    showSystemToast,
    openSettings,
    closeSettings,
    closeSettingsWithAutoSave,
    saveSettings,
    testConnection,
    resetPrompt,
    importCustomFont,
    togglePin,
    handleIconError,
    selectAllSources,
    deselectAllSources,
    backToPrevious,
    closeAppWindow,
    fetchSystemConfig,
    checkApiBalance,
    clearBalanceWarning,
    dismissBalanceWarning,
  }
}

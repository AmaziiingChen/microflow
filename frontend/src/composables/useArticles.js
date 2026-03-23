/**
 * 公文列表、搜索、详情与操作模块 Composable
 * 负责管理文章列表、搜索、详情视图、分页、收藏、下载等功能
 */
import { ref, computed, nextTick, onMounted } from 'vue'
import { usePyWebview } from './usePyWebview'

const pywebview = usePyWebview()

/**
 * 全局时间格式化函数
 */
export const formatDateTime = (dateStr) => {
  if (!dateStr) return ''
  let str = dateStr.trim()
  const match = str.match(
    /(\d{4})[-\/年](\d{1,2})[-\/月](\d{1,2})日?(?:\s+(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?)?/
  )
  if (match) {
    const [, year, month, day, hour, minute] = match
    const pad = (n) => String(n).padStart(2, '0')
    const datePart = `${year}年${pad(month)}月${pad(day)}日`
    if (hour && minute) {
      return `${datePart} ${pad(hour)}:${pad(minute)}`
    }
    return datePart
  }
  return dateStr
}

/**
 * 文章管理模块
 * @param {Object} options - 配置选项
 * @param {import('vue').Ref} options.config - 配置对象引用
 * @param {import('vue').Ref} options.statusMsg - 状态消息引用
 * @param {import('vue').Ref} options.isSettingsOpen - 设置面板状态引用
 * @param {Function} options.closeSettingsWithAutoSave - 关闭设置并保存函数
 * @param {import('vue').Ref} options.isReadOnlyMode - 只读模式引用
 * @param {import('vue').Ref} options.systemNotice - 系统公告引用
 * @param {import('vue').Ref} options.balanceWarningVisible - 欠费卡片可见性引用
 * @param {import('vue').Ref} options.balanceWarning - 欠费卡片内容引用
 * @param {import('vue').Ref} options.isRegenerating - 重新生成状态引用
 * @param {import('vue').Ref} options.aiBrandName - AI 品牌名称引用
 */
export function useArticles(options = {}) {
  const {
    config,
    statusMsg,
    isSettingsOpen,
    closeSettingsWithAutoSave,
    isReadOnlyMode,
    systemNotice,
    balanceWarningVisible,
    balanceWarning,
    isRegenerating,
    aiBrandName,
  } = options

  // ==================== 状态定义 ====================

  // 文章列表
  const articles = ref([])

  // 当前活动文章
  const activeArticle = ref(null)
  const lastArticle = ref(null)

  // 视图状态
  const currentView = ref('list')
  const isNavigatingBack = ref(false)

  // 分页状态
  const page = ref(1)
  const isLoadingMore = ref(false)
  const noMoreData = ref(false)

  // 搜索状态
  const searchQuery = ref('')
  const isSearching = ref(false)
  let searchTimeout = null

  // 来源筛选
  const activeSource = ref('全部')

  // 来源列表（根据订阅动态生成）
  const allAvailableSources = options.allAvailableSources || ref([])

  const sources = computed(() => {
    const subscribed = config?.value?.subscribedSources || []
    if (subscribed.length === 0) {
      return ['全部', ...allAvailableSources.value]
    }
    const ordered = allAvailableSources.value.filter((s) =>
      subscribed.includes(s)
    )
    return ['全部', ...ordered]
  })

  // 灵动 Header 状态
  const isSearchUIExpanded = ref(false)
  const searchInputRef = ref(null)

  // 阅读追踪相关
  const pendingReadUrls = ref([])
  const pendingNoticeVersion = ref(null)
  let readingObserver = null
  let dwellTimer = null
  const userActivityDetected = ref(false)
  const hasDweltEnough = ref(false)
  const isBottomVisible = ref(false)

  // 标签颜色
  const tagColors = ['blue', 'green', 'purple', 'rose', 'amber']

  // ==================== 计算属性 ====================

  /**
   * 获取标签颜色
   */
  const getTagColor = (index) => tagColors[index % tagColors.length]

  /**
   * 处理后的文章列表（包含标签解析、时间分组等）
   */
  const processedArticles = computed(() => {
    if (!articles.value) return []

    let mapped = articles.value.map((item) => {
      // 解析 AI 返回的标签和正文
      let parsedTags = []
      let parsedBody = item.summary || '无总结内容'

      if (parsedBody.includes('【')) {
        const lines = parsedBody.split('\n')
        const firstLine = lines[0].trim()
        if (firstLine.startsWith('【')) {
          const matches = firstLine.match(/【(.*?)】/g)
          if (matches) {
            parsedTags = matches.map((t) => t.replace(/[【】]/g, ''))
          }
          parsedBody = lines.slice(1).join('\n').trim()
        }
      }

      // 解析附件
      let parsedAttachments = []
      try {
        if (item.attachments && item.attachments !== '0' && item.attachments !== '') {
          if (typeof item.attachments === 'string') {
            parsedAttachments = JSON.parse(item.attachments)
          } else if (Array.isArray(item.attachments)) {
            parsedAttachments = item.attachments
          }
        }
      } catch (e) {
        console.error('附件解析失败, 原数据:', item.attachments, e)
        parsedAttachments = []
      }

      const formattedTime = formatDateTime(item.exact_time || item.date)
      return {
        ...item,
        parsedTags,
        parsedBody,
        parsedAttachments,
        formattedTime,
      }
    })

    // 按时间降序排序
    mapped.sort((a, b) => {
      const timeA = a.exact_time || a.date || ''
      const timeB = b.exact_time || b.date || ''
      return timeB.localeCompare(timeA)
    })

    // 订阅过滤
    const subscribedSources = config?.value?.subscribedSources
    if (subscribedSources && subscribedSources.length > 0) {
      mapped = mapped.filter((item) => {
        const sourceName = item.source_name || '公文通'
        return subscribedSources.includes(sourceName)
      })
    }

    // 整合系统公告
    if (
      !isSearching.value &&
      !searchQuery.value.trim() &&
      activeSource.value === '全部' &&
      mapped.length > 0 &&
      systemNotice?.value
    ) {
      const localReadTime = config?.value?.readNoticeTime || ''
      const noticePublishTime = systemNotice.value.publish_time || ''
      const isNoticeRead =
        localReadTime &&
        noticePublishTime &&
        localReadTime >= noticePublishTime

      const noticeItem = {
        id: systemNotice.value.id,
        title: systemNotice.value.title,
        publish_time: systemNotice.value.publish_time,
        department: systemNotice.value.department,
        source_name: systemNotice.value.source_name,
        category: systemNotice.value.category,
        parsedTags: systemNotice.value.tags,
        url: systemNotice.value.url,
        parsedBody: systemNotice.value.content || systemNotice.value.summary,
        summary: systemNotice.value.summary,
        is_announcement: true,
        version: systemNotice.value.version,
        formattedTime: systemNotice.value.publish_time,
        date: systemNotice.value.publish_time,
        is_read: isNoticeRead ? 1 : 0,
      }

      if (isNoticeRead) {
        mapped.push(noticeItem)
      } else {
        mapped.unshift(noticeItem)
      }
    }

    // 欠费卡片
    if (balanceWarningVisible?.value && balanceWarning?.value) {
      const balanceCard = {
        ...balanceWarning.value,
        is_read: 1,
        parsedTags: ['欠费提醒'],
        parsedBody: balanceWarning.value.content,
        summary: balanceWarning.value.content,
        formattedTime: new Date().toLocaleString(),
        source_name: '系统通知',
      }
      mapped.unshift(balanceCard)
    }

    // 时间分组
    const finalGroupedArray = []
    let currentGroup = null

    const getDateGroupName = (dateStr) => {
      if (!dateStr) return '未知时间'
      let cleanStr = dateStr.replace(/年|月/g, '-').replace(/日/g, '')
      let itemDate = new Date(cleanStr)
      if (isNaN(itemDate.getTime())) return '更早'

      const today = new Date()
      today.setHours(0, 0, 0, 0)
      const targetDate = new Date(itemDate)
      targetDate.setHours(0, 0, 0, 0)

      const diffTime = today.getTime() - targetDate.getTime()
      const diffDays = Math.floor(diffTime / (1000 * 60 * 60 * 24))

      if (diffDays === 0) return '今天'
      if (diffDays === 1) return '昨天'
      if (diffDays > 1 && diffDays <= 7) return '过去 7 天'
      if (diffDays > 7 && diffDays <= 30) return '过去 30 天'
      return `${targetDate.getFullYear()}年${targetDate.getMonth() + 1}月`
    }

    mapped.forEach((item) => {
      let groupName = ''
      if (item.id === 'balance_warning' || item.is_announcement) {
        groupName = '系统通知'
      } else {
        groupName = getDateGroupName(item.exact_time || item.date)
      }

      if (groupName !== currentGroup) {
        finalGroupedArray.push({
          isHeader: true,
          title: groupName,
          id: 'header_' + groupName,
        })
        currentGroup = groupName
      }
      finalGroupedArray.push(item)
    })

    return finalGroupedArray
  })

  // ==================== 方法定义 ====================

  /**
   * 加载更多文章
   */
  const loadMoreArticles = async () => {
    isLoadingMore.value = true
    page.value += 1

    let sourceParam = null
    let sourceNamesParam = null
    let favoritesOnly = false

    if (activeSource.value === '全部') {
      const subscribed = config?.value?.subscribedSources || []
      if (subscribed.length > 0) {
        sourceNamesParam = subscribed
      }
    } else if (activeSource.value === '收藏') {
      favoritesOnly = true
      const subscribed = config?.value?.subscribedSources || []
      if (subscribed.length > 0) {
        sourceNamesParam = subscribed
      }
    } else {
      sourceParam = activeSource.value
    }

    const res = await pywebview.getHistoryPaged(
      page.value,
      20,
      sourceParam,
      sourceNamesParam
    )

    // 注意：原始 API 可能不支持 favoritesOnly，这里保留参数以便扩展
    if (res.status === 'success') {
      if (res.data.length === 0) {
        noMoreData.value = true
      } else {
        articles.value.push(...res.data)
      }
    }
    isLoadingMore.value = false
  }

  /**
   * 按来源筛选
   */
  const filterBySource = async (source) => {
    if (isSettingsOpen?.value && closeSettingsWithAutoSave) {
      await closeSettingsWithAutoSave()
    }

    if (activeSource.value === source) return

    // 滚动居中逻辑
    const container = document.querySelector('.filter-scroll-container')
    if (container) {
      const chips = container.querySelectorAll('.filter-chip')
      const sourceIndex = sources.value.indexOf(source)
      const totalSources = sources.value.length

      if (
        sourceIndex > 0 &&
        sourceIndex < totalSources - 2 &&
        chips[sourceIndex]
      ) {
        const chip = chips[sourceIndex]
        const containerWidth = container.clientWidth
        const chipLeft = chip.offsetLeft
        const chipWidth = chip.clientWidth
        const scrollTarget = chipLeft - containerWidth / 2 + chipWidth / 2
        container.scrollTo({
          left: Math.max(0, scrollTarget),
          behavior: 'smooth',
        })
      }
    }

    activeSource.value = source
    page.value = 1
    articles.value = []
    noMoreData.value = false

    let sourceParam = null
    let sourceNamesParam = null

    if (source === '全部') {
      const subscribed = config?.value?.subscribedSources || []
      if (subscribed.length > 0) {
        sourceNamesParam = subscribed
      }
    } else if (source === '收藏') {
      sourceParam = source // 或者使用特定标志
    } else {
      sourceParam = source
    }

    if (isSearching.value || searchQuery.value.trim()) {
      const keyword = searchQuery.value.trim()
      const res = await pywebview.searchArticles(keyword, sourceParam)
      if (res.status === 'success') {
        articles.value = res.data
      }
    } else {
      const res = await pywebview.getHistoryPaged(1, 20, sourceParam, sourceNamesParam)
      if (res.status === 'success') {
        articles.value = res.data
        if (res.data.length < 20) noMoreData.value = true
      }
    }
  }

  /**
   * 处理搜索（带防抖）
   */
  const handleSearch = () => {
    clearTimeout(searchTimeout)
    searchTimeout = setTimeout(async () => {
      const keyword = searchQuery.value.trim()

      if (!keyword) {
        isSearching.value = false
        page.value = 1
        noMoreData.value = false

        let sourceParam = null
        let sourceNamesParam = null
        if (activeSource.value === '全部') {
          const subscribed = config?.value?.subscribedSources || []
          if (subscribed.length > 0) {
            sourceNamesParam = subscribed
          }
        } else {
          sourceParam = activeSource.value
        }

        const res = await pywebview.getHistoryPaged(1, 20, sourceParam, sourceNamesParam)
        if (res.status === 'success') {
          articles.value = res.data
        }
        return
      }

      const sourceParam = activeSource.value !== '全部' ? activeSource.value : null
      isSearching.value = true

      const res = await pywebview.searchArticles(keyword, sourceParam)
      if (res.status === 'success') {
        articles.value = res.data
      }
    }, 300)
  }

  /**
   * 清空搜索
   */
  const clearSearch = () => {
    searchQuery.value = ''
    handleSearch()
  }

  /**
   * 点击搜索放大镜
   */
  const handleSearchClick = () => {
    if (!isSearchUIExpanded.value) {
      isSearchUIExpanded.value = true
      nextTick(() => {
        setTimeout(() => {
          if (searchInputRef.value) searchInputRef.value.focus()
        }, 300)
      })
    } else {
      if (searchQuery.value.trim()) {
        handleSearch()
      } else {
        searchInputRef.value?.focus()
      }
    }
  }

  /**
   * 关闭搜索形变
   */
  const closeSearchUI = () => {
    isSearchUIExpanded.value = false
    clearSearch()
  }

  /**
   * 点击列表区域时关闭搜索
   */
  const handleListClick = (e) => {
    if (!isSearchUIExpanded.value) return

    const searchContainer = document.querySelector('.search-morph-container')
    const searchInput = document.querySelector('.search-input')
    const clickedInSearch =
      searchContainer?.contains(e.target) || searchInput?.contains(e.target)

    const headerButtons = document.querySelector('.main-card-header')
    const clickedInHeader = headerButtons?.contains(e.target)

    if (!clickedInSearch && !clickedInHeader) {
      closeSearchUI()
    }
  }

  /**
   * 今日按钮点击
   */
  const handleTodayClick = async () => {
    if (isSearchUIExpanded.value) {
      closeSearchUI()
    } else {
      if (isSettingsOpen?.value && closeSettingsWithAutoSave) {
        await closeSettingsWithAutoSave()
      }
      filterBySource('全部')
    }
  }

  /**
   * 打开文章详情
   */
  const openDetail = async (item) => {
    let parsedAttachments = item.parsedAttachments || []
    if ((!parsedAttachments || parsedAttachments.length === 0) && item.attachments) {
      try {
        if (item.attachments !== '0' && item.attachments !== '') {
          parsedAttachments = JSON.parse(item.attachments)
        }
      } catch (e) {
        parsedAttachments = []
      }
    }

    const enrichedItem = {
      ...item,
      parsedAttachments: parsedAttachments,
    }
    activeArticle.value = enrichedItem
    lastArticle.value = enrichedItem
    currentView.value = 'detail'

    setupReadingTracker(enrichedItem)
  }

  /**
   * 返回列表
   */
  const backToList = () => {
    isNavigatingBack.value = true
    currentView.value = 'list'
    activeArticle.value = null

    setTimeout(() => {
      if (pendingReadUrls.value.length > 0) {
        pendingReadUrls.value.forEach((url) => {
          const idx = articles.value.findIndex((a) => a.url === url)
          if (idx !== -1) articles.value[idx].is_read = 1
        })
        pendingReadUrls.value = []
      }
      if (pendingNoticeVersion.value) {
        pendingNoticeVersion.value = null
      }
    }, 50)

    setTimeout(() => {
      isNavigatingBack.value = false
    }, 300)
  }

  /**
   * 前进到最后一篇文章
   */
  const goToLastArticle = () => {
    if (lastArticle.value && currentView.value === 'list') {
      activeArticle.value = lastArticle.value
      currentView.value = 'detail'
    }
  }

  /**
   * 处理公告点击
   */
  const handleNoticeClick = async (item) => {
    if (item.is_announcement) {
      activeArticle.value = item
      lastArticle.value = item
      currentView.value = 'detail'

      const localReadTime = config?.value?.readNoticeTime || ''
      const noticePublishTime = item.publish_time || ''
      const isNoticeRead =
        localReadTime && noticePublishTime && localReadTime >= noticePublishTime

      if (!isNoticeRead && config) {
        config.value.readNoticeTime = noticePublishTime
        try {
          await pywebview.saveConfig(JSON.parse(JSON.stringify(config.value)))
        } catch (e) {
          console.warn('保存公告已读状态失败:', e)
        }
      }
    }
  }

  /**
   * 切换收藏状态
   */
  const toggleFavorite = async (item) => {
    if (!item) return

    const oldStatus = item.is_favorite
    const newStatus = oldStatus ? 0 : 1
    item.is_favorite = newStatus

    const articleIndex = articles.value.findIndex((a) => a.url === item.url)
    if (articleIndex !== -1) {
      articles.value[articleIndex] = {
        ...articles.value[articleIndex],
        is_favorite: newStatus,
      }
    }

    try {
      const res = await pywebview.toggleFavorite(item.url)
      if (res.status === 'success') {
        const finalStatus = res.is_favorite ? 1 : 0
        item.is_favorite = finalStatus
        if (articleIndex !== -1) {
          articles.value[articleIndex] = {
            ...articles.value[articleIndex],
            is_favorite: finalStatus,
          }
        }
      } else {
        item.is_favorite = oldStatus
        if (articleIndex !== -1) {
          articles.value[articleIndex] = {
            ...articles.value[articleIndex],
            is_favorite: oldStatus,
          }
        }
      }
    } catch (e) {
      item.is_favorite = oldStatus
      if (articleIndex !== -1) {
        articles.value[articleIndex] = {
          ...articles.value[articleIndex],
          is_favorite: oldStatus,
        }
      }
      console.error('收藏操作失败:', e)
    }
  }

  /**
   * 带动画的收藏切换
   */
  const toggleFavoriteWithAnim = (item, event) => {
    if (!item) return
    const element = event?.currentTarget
    if (element) {
      element.classList.add('is-animating')
      setTimeout(() => element.classList.remove('is-animating'), 600)
    }
    setTimeout(() => toggleFavorite(item), 600)
  }

  /**
   * 下载附件
   */
  const downloadFile = async (att) => {
    const { url, name, download_type } = att
    const type = download_type || 'direct'

    if (type === 'external') {
      try {
        await pywebview.openInBrowser(url)
      } catch (e) {
        pywebview.openBrowser(url)
      }
      return
    }

    if (statusMsg) {
      statusMsg.value = `正在准备下载 ${name}...`
    }

    try {
      const res = await pywebview.downloadAttachment(url, name)
      if (statusMsg) {
        if (res.status === 'success') {
          statusMsg.value = '附件下载成功！'
        } else if (res.status === 'cancelled') {
          statusMsg.value = ''
        } else {
          statusMsg.value = '下载失败: ' + res.message
        }
      }
    } catch (e) {
      if (statusMsg) statusMsg.value = '调起下载失败'
    }

    if (statusMsg) {
      setTimeout(() => (statusMsg.value = ''), 3000)
    }
  }

  /**
   * 打开浏览器
   */
  const openBrowser = (url) => {
    forceMarkRead()
    pywebview.openBrowser(url)
  }

  /**
   * 复制文本
   */
  const copyText = (item) => {
    forceMarkRead()

    let cleanSummary = item.summary || '无总结内容'
    cleanSummary = cleanSummary
      .replace(/<\/?(?:loc|date|contact)[^>]*>/gi, '')
      .replace(/^#+\s+(.*)$/gm, '【$1】')
      .replace(/\*\*(.*?)\*\*/g, '$1')
      .replace(/\*(.*?)\*/g, '$1')
      .replace(/`(.*?)`/g, '$1')

    const textToCopy = `${item.title}\n发布日期：${item.date}\n\n${cleanSummary}\n\n🔗 详情链接：${item.url}`

    navigator.clipboard
      .writeText(textToCopy)
      .then(() => {
        const btn = document.querySelector('.btn-copy')
        if (btn) {
          const oldText = btn.innerText
          btn.innerText = '复制成功'
          setTimeout(() => (btn.innerText = oldText), 2000)
        }
      })
      .catch((err) => {
        console.error('复制失败', err)
        if (statusMsg) statusMsg.value = '复制失败，请检查浏览器权限'
      })
  }

  /**
   * 滚动到附件区
   */
  const scrollToAttachments = () => {
    const el = document.getElementById('attachments-area')
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
  }

  /**
   * 截断文件名（中间省略）
   */
  const truncateMiddle = (filename) => {
    if (!filename) return { start: '', end: '', suffix: '', short: true }

    const lastDotIndex = filename.lastIndexOf('.')
    let mainPart = filename
    let suffix = ''

    if (lastDotIndex !== -1 && lastDotIndex !== 0) {
      suffix = filename.slice(lastDotIndex)
      mainPart = filename.slice(0, lastDotIndex)
    }

    if (mainPart.length <= 10) {
      return { start: mainPart, end: '', suffix, short: true }
    }

    const endLen = 4
    const start = mainPart.slice(0, -endLen)
    const end = mainPart.slice(-endLen)

    return { start, end, suffix, short: false }
  }

  /**
   * 获取附件图标类型
   */
  const getAttachmentIcon = (filename) => {
    if (!filename) return null
    const ext = filename.split('.').pop().toLowerCase()
    if (ext === 'pdf') return 'pdf'
    if (['doc', 'docx', 'wps'].includes(ext)) return 'doc'
    if (['ppt', 'pptx'].includes(ext)) return 'ppt'
    if (['xls', 'xlsx', 'csv'].includes(ext)) return 'xls'
    if (['png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp', 'svg', 'ico', 'tiff', 'tif'].includes(ext)) return 'pic'
    if (['zip', '7z', 'rar', 'tar', 'gz', 'bz2', 'xz'].includes(ext)) return 'zip'
    if (['txt', 'text', 'log', 'md', 'markdown'].includes(ext)) return 'txt'
    return null
  }

  // 附件截断状态
  const prefixRefs = {}
  const truncatedStates = ref({})

  const setPrefixRef = (el, url) => {
    if (el) {
      prefixRefs[url] = el
      setTimeout(() => checkTruncation(url), 0)
    }
  }

  const setAttNameRef = (el, url) => {
    if (el) {
      const resizeObserver = new ResizeObserver(() => {
        checkTruncation(url)
      })
      resizeObserver.observe(el)
    }
  }

  const checkTruncation = (url) => {
    const el = prefixRefs[url]
    if (el) {
      const isTruncated = el.scrollWidth > el.clientWidth
      truncatedStates.value[url] = isTruncated
    }
  }

  const isPrefixTruncated = (url) => {
    return truncatedStates.value[url] || false
  }

  /**
   * 渲染 Markdown
   */
  const renderMarkdown = (text) => {
    if (!text) return '无总结内容'
    // 需要外部注入 marked 库
    if (typeof marked !== 'undefined') {
      return marked.parse(text)
    }
    return text
  }

  // ==================== 阅读追踪 ====================

  const handleUserActivity = () => {
    if (!userActivityDetected.value) {
      userActivityDetected.value = true
      dwellTimer = setTimeout(() => {
        hasDweltEnough.value = true
        checkAndMarkRead()
      }, 2000)
    }
  }

  const checkAndMarkRead = () => {
    if (
      hasDweltEnough.value &&
      isBottomVisible.value &&
      activeArticle.value &&
      activeArticle.value.is_read === 0
    ) {
      const url = activeArticle.value.url
      if (!pendingReadUrls.value.includes(url)) {
        pendingReadUrls.value.push(url)
        pywebview.markAsRead && pywebview.markAsRead(url).catch((e) => console.error(e))
        console.log('✅ 卡片已在后台静默标记为已读')
      }
    }
  }

  const setupReadingTracker = (item) => {
    if (readingObserver) {
      readingObserver.disconnect()
      readingObserver = null
    }
    if (dwellTimer) {
      clearTimeout(dwellTimer)
      dwellTimer = null
    }
    window.removeEventListener('mousemove', handleUserActivity)
    window.removeEventListener('keydown', handleUserActivity)
    window.removeEventListener('wheel', handleUserActivity)

    if (item.is_read !== 0) return

    userActivityDetected.value = false
    hasDweltEnough.value = false
    isBottomVisible.value = false

    window.addEventListener('mousemove', handleUserActivity, { once: true })
    window.addEventListener('keydown', handleUserActivity, { once: true })
    window.addEventListener('wheel', handleUserActivity, { once: true })

    setTimeout(() => {
      const detector = document.getElementById('read-detector')
      if (detector) {
        readingObserver = new IntersectionObserver(
          (entries) => {
            if (entries[0].isIntersecting) {
              isBottomVisible.value = true
              checkAndMarkRead()
            } else {
              isBottomVisible.value = false
            }
          },
          { threshold: 0.1 }
        )
        readingObserver.observe(detector)
      }
    }, 400)
  }

  const forceMarkRead = () => {
    if (activeArticle.value && activeArticle.value.is_read === 0) {
      const url = activeArticle.value.url
      if (!pendingReadUrls.value.includes(url)) {
        pendingReadUrls.value.push(url)
        pywebview.markAsRead && pywebview.markAsRead(url).catch((e) => console.error(e))
      }
    }
  }

  // ==================== 快照与重新生成 ====================

  /**
   * 保存长图快照 (Snapshot)
   * 终极护城河版：彻底解绑 viewport 锁定 & Base64字体穿透
   */
  const takeSnapshot = async (item) => {
    const oldMsg = statusMsg?.value || ''
    if (statusMsg) statusMsg.value = '正在生成高清长截图，请稍候...'

    const targetView = document.querySelector('.detail-view')
    const scrollContent = document.querySelector('.detail-content')

    if (!targetView || !scrollContent) return

    // 🌟 终极修复：沙盒穿透！将外部字体转化为 Base64 纯文本注入
    let tempBase64Style = null
    if (config?.value?.fontFamily === 'custom' && config?.value?.customFontPath) {
      try {
        if (statusMsg) statusMsg.value = '正在打包字体资源...'
        const fontUrl = new URL(config.value.customFontPath, window.location.href).href
        const response = await fetch(fontUrl)
        const blob = await response.blob()

        // 将字体文件转为 Base64 字符串格式
        const base64Data = await new Promise((resolve) => {
          const reader = new FileReader()
          reader.onloadend = () => resolve(reader.result)
          reader.readAsDataURL(blob)
        })

        // 动态创建一个带 Base64 字体的 Style 标签
        tempBase64Style = document.createElement('style')
        tempBase64Style.id = 'temp-snapshot-font'
        tempBase64Style.innerHTML = `
          @font-face {
            font-family: 'UserCustomFont';
            src: url(${base64Data}) !important;
          }
        `
        document.head.appendChild(tempBase64Style)

        // 停顿 150ms 让浏览器重新排版应用 Base64 字体
        await new Promise((resolve) => setTimeout(resolve, 150))
      } catch (err) {
        console.warn('❌ 字体打包为 Base64 失败:', err)
      }
    }

    if (statusMsg) statusMsg.value = '正在生成高清长截图，请稍候...'

    // 1. 记录所有原始样式
    const origBodyHeight = document.body.style.height
    const origBodyOverflow = document.body.style.overflow

    const origPos = targetView.style.position
    const origBottom = targetView.style.bottom
    const origHeight = targetView.style.height
    const origViewOverflow = targetView.style.overflow
    const origBg = targetView.style.backgroundColor
    const origWidth = targetView.style.width

    const origContentOverflow = scrollContent.style.overflowY
    const origContentFlex = scrollContent.style.flex

    // 2. 彻底解除屏幕束缚
    document.body.style.height = 'auto'
    document.body.style.overflow = 'visible'

    // 核心修复：在解除 fixed 之前，强行把现在的像素宽度"焊死"在行内样式上
    targetView.style.width = targetView.offsetWidth + 'px'
    targetView.style.position = 'absolute'
    targetView.style.bottom = 'auto'
    targetView.style.height = 'auto'
    targetView.style.overflow = 'visible'
    targetView.style.backgroundColor = '#ffffff'

    scrollContent.style.overflowY = 'visible'
    scrollContent.style.flex = 'none'

    // 3. 冻结动画，防止图标消失
    document.body.classList.add('is-capturing')

    // 排版缓冲
    await new Promise((resolve) => setTimeout(resolve, 800))
    await document.fonts.ready

    try {
      console.log('🎨 [Snapshot] 正在使用 foreignObjectRendering 引擎进行原生 SVG 渲染...')

      // 4. 调用 html2canvas (SVG 引擎)
      // 注意：html2canvas 需要全局可用
      const html2canvasLib = window.html2canvas
      if (!html2canvasLib) {
        throw new Error('html2canvas 库未加载')
      }

      const canvas = await html2canvasLib(targetView, {
        scale: 2,
        useCORS: true,
        backgroundColor: '#ffffff',
        allowTaint: false,
        foreignObjectRendering: true,
        windowWidth: targetView.scrollWidth,
        windowHeight: targetView.scrollHeight,
      })

      // 5. 导出并保存
      const base64image = canvas.toDataURL('image/png')
      const res = await pywebview.saveSnapshot(base64image, item?.title || 'snapshot')

      if (res && res.status === 'success') {
        console.log('✅ [Snapshot] 文件保存成功，正在写入剪贴板...')

        // 追加调用剪贴板 API
        const copyRes = await pywebview.copyImageToClipboard(base64image)

        if (copyRes && copyRes.status === 'success') {
          if (statusMsg) statusMsg.value = '快照保存成功，并已复制到剪贴板！'
        } else {
          if (statusMsg) statusMsg.value = '快照已保存 (但复制到剪贴板失败)'
        }
      } else if (res && res.status === 'cancelled') {
        if (statusMsg) statusMsg.value = ''
      } else {
        console.error('❌ [Snapshot] 后端保存失败:', res)
        if (statusMsg) statusMsg.value = '保存失败: ' + (res.message || '未知错误')
      }
    } catch (error) {
      console.error('💥 [Snapshot] 前端捕获失败:', error)
      if (statusMsg) statusMsg.value = '截图失败: ' + error.message
    } finally {
      // 6. 恢复所有原始样式
      document.body.style.height = origBodyHeight
      document.body.style.overflow = origBodyOverflow

      targetView.style.position = origPos
      targetView.style.bottom = origBottom
      targetView.style.height = origHeight
      targetView.style.overflow = origViewOverflow
      targetView.style.backgroundColor = origBg
      targetView.style.width = origWidth

      scrollContent.style.overflowY = origContentOverflow
      scrollContent.style.flex = origContentFlex

      document.body.classList.remove('is-capturing')

      // 卸载临时注入的 Base64 字体
      if (tempBase64Style) tempBase64Style.remove()

      console.log('🔄 [Snapshot] CSS 布局与字体已恢复正常。')
      setTimeout(() => {
        if (statusMsg) statusMsg.value = oldMsg
      }, 3000)
    }
  }

  /**
   * 重新生成 AI 总结
   */
  const regenerateSummary = async (article) => {
    const articleId = article?.id
    if (!articleId) return

    // 防止重复点击
    if (isRegenerating?.value) return

    // 设置刷新状态
    if (isRegenerating) isRegenerating.value = true
    const brandName = aiBrandName?.value || 'AI'
    if (statusMsg) statusMsg.value = `正在重新生成 ${brandName} 总结...`

    try {
      const res = await pywebview.regenerateSummary(articleId)

      if (res.status === 'success') {
        // 重新解析标签和正文
        let parsedTags = []
        let parsedBody = res.summary || '无总结内容'

        if (parsedBody.includes('【')) {
          const lines = parsedBody.split('\n')
          const firstLine = lines[0].trim()
          if (firstLine.startsWith('【')) {
            const matches = firstLine.match(/【(.*?)】/g)
            if (matches) {
              parsedTags = matches.map((t) => t.replace(/[【】]/g, ''))
            }
            parsedBody = lines.slice(1).join('\n').trim()
          }
        }

        // 构建更新后的文章对象
        const updatedArticle = {
          ...article,
          summary: res.summary,
          parsedTags,
          parsedBody,
        }

        // 更新当前详情视图
        activeArticle.value = updatedArticle

        // 同步更新列表 articles 中的对应项
        const index = articles.value.findIndex((a) => a.id === articleId)
        if (index !== -1) {
          articles.value[index] = {
            ...articles.value[index],
            ...updatedArticle,
          }
        }

        if (statusMsg) statusMsg.value = `${brandName} 总结已重新生成！`
      } else {
        if (statusMsg) statusMsg.value = res.message || '生成失败'
      }
    } catch (e) {
      console.error('重新生成总结失败:', e)
      const brandName = aiBrandName?.value || 'AI'
      if (statusMsg) statusMsg.value = `${brandName} 请求失败，请重试`
    } finally {
      // 清除刷新状态
      if (isRegenerating) isRegenerating.value = false
      setTimeout(() => {
        if (statusMsg) statusMsg.value = ''
      }, 3000)
    }
  }

  // ==================== 返回 ====================

  return {
    // 状态
    articles,
    activeArticle,
    lastArticle,
    currentView,
    isNavigatingBack,
    processedArticles,
    page,
    isLoadingMore,
    noMoreData,
    searchQuery,
    isSearching,
    activeSource,
    sources,
    isSearchUIExpanded,
    searchInputRef,
    pendingReadUrls,

    // 方法
    loadMoreArticles,
    filterBySource,
    handleSearch,
    clearSearch,
    handleSearchClick,
    closeSearchUI,
    handleListClick,
    handleTodayClick,
    openDetail,
    backToList,
    goToLastArticle,
    handleNoticeClick,
    toggleFavorite,
    toggleFavoriteWithAnim,
    downloadFile,
    openBrowser,
    copyText,
    scrollToAttachments,
    truncateMiddle,
    getAttachmentIcon,
    setPrefixRef,
    setAttNameRef,
    isPrefixTruncated,
    renderMarkdown,
    setupReadingTracker,
    forceMarkRead,
    takeSnapshot,
    regenerateSummary,
    getTagColor,
    formatDateTime,
  }
}

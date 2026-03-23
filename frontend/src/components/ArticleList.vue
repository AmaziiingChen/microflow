<template>
  <!-- 文章列表组件 -->
  <div class="article-list-wrapper">
    <!-- 筛选栏：收藏模式和设置模式下隐藏 -->
    <div
      v-if="activeSource !== '收藏' && !isSettingsOpen"
      class="filter-scroll-container"
    >
      <div
        v-for="src in sources"
        :key="src"
        :class="['filter-chip', { active: activeSource === src }]"
        @click="$emit('filter-by-source', src)"
      >
        {{ src }}
      </div>
    </div>

    <!-- 收藏模式标题 -->
    <div
      v-if="activeSource === '收藏' && !isSettingsOpen"
      class="favorite-title"
    >
      收藏
    </div>

    <transition-group name="list-pop" tag="div" style="position: relative">
      <!-- 设置面板插槽 -->
      <slot name="settings"></slot>

      <!-- 文章卡片列表 -->
      <template v-if="!isSettingsOpen">
        <div
          v-for="(item, index) in processedArticles"
          :key="item.isHeader ? 'header_' + item.title : (item.id || item.url)"
        >
          <!-- 日期分组标题 -->
          <div v-if="item.isHeader" class="date-group-header">
            {{ item.title }}
          </div>

          <!-- 文章卡片 -->
          <div
            v-else
            :class="['list-card', { 'announcement-card': item.is_announcement, 'read': item.is_read && item.is_announcement }]"
            @click="item.is_announcement ? $emit('notice-click', item) : $emit('open-detail', item)"
          >
            <div class="title-row">
              <h3
                class="list-card-title"
                :class="item.is_read === 0 ? 'unread' : 'read'"
              >
                <!-- 公告图标 -->
                <span v-if="item.is_announcement" class="announcement-icon">📢</span>
                {{ item.title }}
              </h3>
              <!-- 附件图标：仅当有附件时显示，公告不显示 -->
              <span
                v-if="!item.is_announcement && item.parsedAttachments && item.parsedAttachments.length > 0"
                class="attachment-icon"
                :title="'共 ' + item.parsedAttachments.length + ' 个附件'"
              >
                <svg
                  viewBox="0 0 1024 1024"
                  xmlns="http://www.w3.org/2000/svg"
                >
                  <path
                    d="M147.328 0C101.504 0 64 36.864 64 81.92v860.16C64 987.136 101.504 1024 147.328 1024h729.344c45.824 0 83.328-36.864 83.328-81.92V286.72L668.288 0h-520.96z"
                    fill="#BBC6FF"
                  />
                  <path
                    d="M750.592 293.568h208L667.392 2.304v208c0 45.824 37.376 83.2 83.2 83.2M512 352c0 17.6-14.4 32-32 32h-256a32.064 32.064 0 0 1-32-32c0-17.6 14.4-32 32-32h256c17.6 0 32 14.4 32 32m256 192c0 17.6-13.888 32-30.848 32H222.848A31.552 31.552 0 0 1 192 544c0-17.6 13.888-32 30.848-32h514.304c16.96 0 30.848 14.4 30.848 32m64 192c0 17.6-15.424 32-34.304 32H226.304C207.36 768 192 753.6 192 736s15.36-32 34.304-32h571.392c18.88 0 34.304 14.4 34.304 32"
                    fill="#8395FF"
                  />
                </svg>
              </span>
              <!-- 收藏图标：只读显示，仅在已收藏时出现 -->
              <span
                v-if="!item.is_announcement && item.is_favorite"
                class="favorite-icon is-favorite"
                title="已收藏"
                style="color: #f5b014"
              >
                <svg
                  t="1774199426448"
                  class="icon"
                  viewBox="0 0 1024 1024"
                  version="1.1"
                  xmlns="http://www.w3.org/2000/svg"
                  p-id="91854"
                  width="200"
                  height="200"
                >
                  <path
                    d="M512 0a57.051429 57.051429 0 0 0-51.931429 33.645714L346.697143 274.285714a58.514286 58.514286 0 0 1-43.885714 32.914286l-253.074286 38.034286a61.44 61.44 0 0 0-32.182857 103.131428l183.588571 187.245715a64.365714 64.365714 0 0 1 18.285714 53.394285L174.08 950.857143a59.977143 59.977143 0 0 0 57.051429 73.142857 54.125714 54.125714 0 0 0 27.062857-7.314286l226.742857-124.342857a53.394286 53.394286 0 0 1 54.125714 0l226.742857 124.342857a54.125714 54.125714 0 0 0 27.062857 7.314286 59.977143 59.977143 0 0 0 57.051429-73.142857L804.571429 689.005714a64.365714 64.365714 0 0 1 16.822857-53.394285l183.588571-187.245715a61.44 61.44 0 0 0-32.182857-103.131428L721.188571 307.2a58.514286 58.514286 0 0 1-43.885714-32.914286L563.931429 33.645714A57.051429 57.051429 0 0 0 512 0z"
                    fill="#FCCF07"
                    p-id="91855"
                  ></path>
                </svg>
              </span>
            </div>

            <!-- 元数据行：AI 标签 + 来源 + 分类 + 时间 -->
            <div class="article-meta">
              <!-- AI 标签：只渲染第一个，使用莫兰迪配色循环 -->
              <span
                v-if="item.parsedTags && item.parsedTags.length > 0"
                :class="['tag', item.is_announcement ? 'tag-blue' : 'morandi-' + (index % 8), { 'tag-dimmed': item.is_read && !item.is_announcement }]"
              >
                {{ item.parsedTags[0] }}
              </span>
              <!-- 部门：优先显示（公文通场景） -->
              <span class="meta-tag-gray" v-if="item.department">{{ item.department }}</span>
              <!-- 来源：非公文通且不与部门重复时显示 -->
              <span
                class="meta-tag-gray"
                v-if="item.source_name && item.source_name !== '公文通' && item.source_name !== item.department"
              >{{ item.source_name }}</span>
              <!-- 分类：不与部门、来源重复时显示 -->
              <span
                class="meta-tag-gray"
                v-if="item.category && item.category !== item.department && item.category !== item.source_name"
              >{{ item.category }}</span>
              <!-- 精确时间：右对齐，永远中灰 -->
              <span class="list-date">{{ item.formattedTime || item.exact_time || item.date }}</span>
            </div>

            <!-- 欠费卡片专属按钮区域 -->
            <div
              v-if="item.id === 'balance_warning'"
              style="
                margin-top: 10px;
                display: flex;
                gap: 10px;
                justify-content: flex-end;
                padding: 0 4px;
              "
            >
              <button
                class="action-btn btn-primary"
                @click.stop="$emit('clear-balance-warning')"
                style="padding: 6px 14px; font-size: 12px"
              >
                {{ item.button_text }}
              </button>
              <button
                class="action-btn btn-secondary"
                @click.stop="$emit('dismiss-balance-warning')"
                style="padding: 6px 14px; font-size: 12px"
              >
                不再提醒
              </button>
            </div>
          </div>
        </div>
      </template>
    </transition-group>

    <!-- 加载更多锚点 -->
    <div class="list-view" style="padding-top: 0; min-height: 40px; overflow: hidden">
      <div id="load-more-anchor">
        <span v-if="isLoadingMore">加载更多中...</span>
        <span v-else-if="isSearching">
          找到 {{ processedArticles.filter(a => !a.is_announcement && !a.isHeader).length }} 篇相关条目
        </span>
        <span v-else-if="noMoreData">
          共 {{ processedArticles.filter(a => !a.is_announcement && !a.isHeader).length }} 篇条目
        </span>
      </div>
    </div>
  </div>
</template>

<script setup>
// 文章卡片列表组件
const props = defineProps({
  // 当前选中的来源
  activeSource: {
    type: String,
    default: '全部'
  },
  // 是否打开设置面板
  isSettingsOpen: {
    type: Boolean,
    default: false
  },
  // 来源列表
  sources: {
    type: Array,
    default: () => []
  },
  // 处理后的文章列表（包含日期分组标题）
  processedArticles: {
    type: Array,
    default: () => []
  },
  // 是否正在加载更多
  isLoadingMore: {
    type: Boolean,
    default: false
  },
  // 是否正在搜索
  isSearching: {
    type: Boolean,
    default: false
  },
  // 是否没有更多数据
  noMoreData: {
    type: Boolean,
    default: false
  }
})

// 定义事件
const emit = defineEmits([
  'filter-by-source',    // 筛选来源
  'notice-click',        // 点击公告
  'open-detail',         // 打开文章详情
  'clear-balance-warning', // 清除欠费警告
  'dismiss-balance-warning' // 忽略欠费警告
])
</script>

<style scoped>
/* 来源筛选栏样式 */
.filter-scroll-container {
  display: flex;
  overflow-x: auto;
  padding: 0 0 0px 0;
  margin-bottom: 12px;
  gap: 8px;
  scrollbar-width: none;
  scroll-behavior: smooth;
}

.filter-scroll-container::-webkit-scrollbar {
  display: none;
}

.filter-chip {
  padding: 6px 16px;
  border-radius: 20px;
  background: rgba(255, 255, 255, 0.85);
  font-size: 13px;
  font-weight: 600;
  color: #4b5563;
  border: 1px solid transparent;
  white-space: nowrap;
  flex-shrink: 0;
  cursor: pointer;
  user-select: none;
  transition:
    transform 0.2s ease,
    background-color 0.2s ease,
    color 0.2s ease;
  transform-origin: center;
}

.filter-chip.active {
  background: rgba(52, 120, 246, 0.1);
  color: var(--accent-color);
  border-color: rgba(52, 120, 246, 0.3);
}

.filter-chip:hover:not(.active) {
  background: #e5e7eb;
}

.filter-chip:active {
  background: var(--btn-bg);
  color: #ffffff;
  transform: scale(1.08);
  box-shadow: 0 4px 12px rgba(87, 155, 240, 0.25);
}

/* 收藏模式标题 */
.favorite-title {
  font-size: 18px;
  font-weight: 800;
  color: #111827;
  margin-bottom: 12px;
  padding: 0 4px;
}

/* 日期分组标题 */
.date-group-header {
  font-size: 18px;
  font-weight: 800;
  color: #111827;
  margin: 20px 0 12px 0;
  padding: 0 4px;
  position: sticky;
  top: 0;
  background: var(--card-bg);
  z-index: 10;
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
}

.date-group-header:first-of-type {
  margin-top: 4px;
}

/* 列表卡片基础样式 */
.list-card {
  background: var(--card-bg);
  border-radius: 16px;
  padding: 16px 20px;
  margin-bottom: 12px;
  cursor: pointer;
  position: relative;
  display: flex;
  flex-direction: column;
  border: 1px solid rgba(0, 0, 0, 0.03);
  box-shadow:
    0 1px 3px rgba(0, 0, 0, 0.02),
    0 4px 12px rgba(0, 0, 0, 0.02);
  will-change: transform, box-shadow;
  transition:
    transform 0.4s var(--curve-pop),
    box-shadow 0.4s ease,
    border-color 0.4s ease,
    background-color 0.3s ease;
}

.list-card:hover {
  transform: translateY(-2px);
  border-color: rgba(0, 0, 0, 0.06);
  background-color: #ffffff;
  box-shadow:
    0 4px 8px rgba(0, 0, 0, 0.03),
    0 8px 16px rgba(0, 0, 0, 0.05);
}

.list-card:active {
  transform: translateY(0) scale(0.98);
  background-color: #fafafa;
  box-shadow:
    0 1px 2px rgba(0, 0, 0, 0.03),
    0 2px 4px rgba(0, 0, 0, 0.02);
  transition: transform 0.1s;
}

/* 公告卡片样式 */
.announcement-card {
  background: var(--card-bg);
  border-radius: 16px;
  padding: 16px 20px;
  margin-bottom: 12px;
  cursor: pointer;
  position: relative;
  display: flex;
  flex-direction: column;
  border: 1px solid rgba(0, 0, 0, 0.03);
  box-shadow:
    0 1px 3px rgba(0, 0, 0, 0.02),
    0 4px 12px rgba(0, 0, 0, 0.02);
  will-change: transform, box-shadow;
  transition:
    transform 0.4s var(--curve-pop),
    box-shadow 0.4s ease,
    border-color 0.4s ease,
    background-color 0.3s ease;
}

.announcement-card:hover {
  transform: translateY(-2px);
  border-color: rgba(0, 0, 0, 0.06);
  background-color: #ffffff;
  box-shadow:
    0 4px 8px rgba(0, 0, 0, 0.03),
    0 8px 16px rgba(0, 0, 0, 0.05);
}

.announcement-card:active {
  transform: translateY(0) scale(0.98);
  background-color: #fafafa;
  box-shadow:
    0 1px 2px rgba(0, 0, 0, 0.03),
    0 2px 4px rgba(0, 0, 0, 0.02);
  transition: transform 0.1s;
}

.announcement-card.read {
  opacity: 0.7;
  filter: grayscale(0.2);
  border-left-color: #d1d5db;
}

.announcement-card.read .announcement-icon {
  filter: grayscale(0.5);
}

.announcement-icon {
  font-size: 1.3rem;
  margin-right: 8px;
  filter: drop-shadow(0 1px 2px rgba(0, 0, 0, 0.1));
}

.list-card.announcement-card .list-card-title {
  font-weight: 700;
  color: #b45309;
}

/* 标题行 */
.title-row {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  margin-bottom: 12px;
}

.title-row .list-card-title {
  flex: 1;
  margin: 0;
}

/* 附件图标 */
.attachment-icon {
  flex-shrink: 0;
  width: 16px;
  height: 16px;
  opacity: 0.5;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  margin-top: 4px;
}

.attachment-icon svg {
  width: 16px;
  height: 16px;
}

.attachment-icon:hover {
  opacity: 1;
}

/* 收藏图标 */
.favorite-icon {
  flex-shrink: 0;
  width: 18px;
  height: 18px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  margin-top: 3px;
  margin-left: 4px;
}

.favorite-icon svg {
  width: 16px;
  height: 16px;
}

/* 卡片标题 */
.list-card-title {
  font-size: 16px;
  font-weight: 700;
  margin: 0;
  line-height: 1.5;
  transition: color 0.3s ease;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  word-break: break-all;
  overflow-wrap: break-word;
}

.list-card-title.unread {
  color: #b72a5c;
}

.list-card-title.read {
  color: #959ca9;
}

/* 元数据行 */
.article-meta {
  display: flex;
  flex-wrap: nowrap;
  gap: 10px;
  align-items: center;
  font-size: 12px;
  min-width: 0;
}

.meta-tag-gray {
  background-color: #f3f4f6;
  color: #959aa4;
  padding: 0 6px;
  height: 22px;
  border-radius: 6px;
  font-size: 11px;
  font-weight: 700;
  border: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex-shrink: 1;
}

.list-card.read .article-meta {
  opacity: 0.75;
}

.list-card.read .meta-tag-gray {
  background-color: #f0f0f0;
  color: #d5cece;
}

/* 日期 */
.list-date {
  margin-left: auto;
  font-weight: 600;
  color: #9ca3af;
  white-space: nowrap;
  font-size: 12px;
  flex-shrink: 0;
}

/* 标签样式 */
.tag {
  padding: 0 6px;
  height: 22px;
  border-radius: 6px;
  font-size: 11px;
  font-weight: 700;
  white-space: nowrap;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.tag-blue {
  background-color: #dbeafe;
  color: #1d4ed8;
}

.tag-dimmed {
  opacity: 0.6;
}

/* 莫兰迪配色 */
.tag.morandi-0 {
  background-color: #e8d4d4;
  color: #8b6b6b;
}

.tag.morandi-1 {
  background-color: #d4e0e8;
  color: #5b7a8c;
}

.tag.morandi-2 {
  background-color: #d4e4d8;
  color: #5b8066;
}

.tag.morandi-3 {
  background-color: #e0d8e8;
  color: #7b6b8c;
}

.tag.morandi-4 {
  background-color: #f0e8d8;
  color: #8b7b5b;
}

.tag.morandi-5 {
  background-color: #e8dcc8;
  color: #8b7a5b;
}

.tag.morandi-6 {
  background-color: #d8e0e0;
  color: #6b8080;
}

.tag.morandi-7 {
  background-color: #e4dcd4;
  color: #7b6b5b;
}

/* 列表动画 */
.list-pop-move,
.list-pop-enter-active,
.list-pop-leave-active {
  transition: all 0.5s var(--curve-pop);
}

.list-pop-enter-from {
  opacity: 0;
  transform: translateY(20px) scale(0.96);
}

.list-pop-leave-to {
  opacity: 0;
  transform: translateY(-20px) scale(0.96);
}

.list-pop-leave-active {
  position: absolute;
  width: 100%;
  z-index: 0;
  pointer-events: none;
}

.list-pop-enter-active {
  z-index: 1;
}

/* 按钮样式 */
.action-btn {
  padding: 8px 16px;
  border-radius: 8px;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.2s ease;
  border: none;
}

.btn-primary {
  background: var(--accent-color);
  color: white;
}

.btn-primary:hover {
  background: #1d4ed8;
}

.btn-secondary {
  background: #f3f4f6;
  color: #374151;
}

.btn-secondary:hover {
  background: #e5e7eb;
}

/* 加载更多锚点 */
#load-more-anchor {
  text-align: center;
  color: var(--text-sub);
  font-size: 13px;
  padding: 16px;
}
</style>

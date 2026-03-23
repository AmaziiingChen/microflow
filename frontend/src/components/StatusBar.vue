<template>
  <!-- 状态与进度条组件 -->
  <div class="status-bar-wrapper">
    <!-- 🎯 爬虫进度条 -->
    <div
      v-if="isLoading && updatePhase === 'spider'"
      style="margin-bottom: 8px; padding: 0 4px"
    >
      <div
        style="
          display: flex;
          justify-content: space-between;
          align-items: center;
          font-size: 11px;
          color: #6b7280;
          margin-bottom: 4px;
          font-weight: 600;
        "
      >
        <div class="progress-left-text">
          {{ spiderProgress.detail || (spiderProgress.completed === 0 ? '正在启动...' : '') }}
        </div>
        <div class="progress-right-stats">
          <!-- 🌟 爬虫阶段只显示已扫描数量，不显示预估值 -->
          <span v-if="spiderProgress.completed > 0">
            {{ spiderProgress.completed }}
          </span>
          <span v-else-if="spiderProgress.total > 0 && spiderProgress.completed === 0">
            准备中...
          </span>
        </div>
      </div>
      <div class="progress-track">
        <!-- 🌟 爬虫阶段使用脉冲动画，不显示具体百分比 -->
        <div class="progress-fill progress-indeterminate"></div>
      </div>
    </div>

    <!-- 🎯 AI 总结进度条 -->
    <div
      v-if="isLoading && updatePhase === 'ai'"
      style="margin-bottom: 8px; padding: 0 4px"
    >
      <!-- 🌟 标题行：只显示文章标题，不显示其他文字 -->
      <div
        style="
          font-size: 11px;
          color: #6b7280;
          margin-bottom: 1px;
          font-weight: 600;
        "
      >
        <div
          class="progress-left-text"
          style="
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
          "
        >
          {{ aiProgress.detail || '等待处理...' }}
        </div>
      </div>
      <!-- 进度条 -->
      <div class="progress-track">
        <div
          class="progress-fill"
          :style="{ width: (aiProgress.total > 0 ? Math.min(aiProgress.completed / aiProgress.total * 100, 100) : 0) + '%' }"
        ></div>
      </div>
      <!-- 🌟 百分比和数量：放在进度条下方右侧 -->
      <div
        style="
          display: flex;
          justify-content: flex-end;
          font-size: 10px;
          color: #9ca3af;
          margin-top: 0px;
          line-height: 1;
        "
        v-if="aiProgress.total > 0"
      >
        <template v-if="aiProgress.completed >= aiProgress.total">
          100% ( {{ aiProgress.total }} / {{ aiProgress.total }} )
        </template>
        <template v-else>
          {{ Math.round(aiProgress.completed / aiProgress.total * 100) }}% ( {{ aiProgress.completed }} / {{ aiProgress.total }} )
        </template>
      </div>
    </div>

    <!-- 全局状态消息 -->
    <div v-if="statusMsg" class="status-center">{{ statusMsg }}</div>
  </div>
</template>

<script setup>
// 状态与进度条组件
const props = defineProps({
  // 是否正在加载
  isLoading: {
    type: Boolean,
    default: false
  },
  // 更新阶段：'idle', 'spider', 'ai'
  updatePhase: {
    type: String,
    default: 'idle'
  },
  // 爬虫进度 { total: number, completed: number, detail: string, currentSource?: string }
  spiderProgress: {
    type: Object,
    default: () => ({ total: 0, completed: 0, detail: '', currentSource: '' })
  },
  // AI 进度 { total: number, completed: number, detail: string }
  aiProgress: {
    type: Object,
    default: () => ({ total: 0, completed: 0, detail: '' })
  },
  // 全局状态消息
  statusMsg: {
    type: String,
    default: ''
  }
})
</script>

<style scoped>
/* 进度条外框 */
.progress-track {
  height: 4px;
  background: #e5e7eb;
  border-radius: 2px;
  margin-bottom: 1px;
  overflow: hidden;
  width: 100%;
}

/* 进度条内芯 (带平滑动画) */
.progress-fill {
  height: 100%;
  background: var(--accent-color);
  width: 0%;
  transition: width 0.4s cubic-bezier(0.4, 0, 0.2, 1);
}

/* 爬虫阶段的不确定进度动画（脉冲效果） */
.progress-indeterminate {
  width: 30% !important;
  animation: progress-pulse 1.5s ease-in-out infinite;
}

@keyframes progress-pulse {
  0% {
    width: 10%;
    opacity: 0.6;
  }
  50% {
    width: 50%;
    opacity: 1;
  }
  100% {
    width: 10%;
    opacity: 0.6;
  }
}

/* 进度条左侧文本（自动省略） */
.progress-left-text {
  flex: 1;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  min-width: 0;
}

/* 进度条右侧数字（强制不换行） */
.progress-right-stats {
  white-space: nowrap;
  flex-shrink: 0;
  margin-left: 12px;
}

/* 状态消息 */
.status-center {
  text-align: center;
  color: var(--text-sub);
  font-size: 13px;
  margin-top: 10px;
}
</style>

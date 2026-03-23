<template>
  <!-- 设置面板 -->
  <div class="list-card inline-settings-card">
    <div
      style="
        border-bottom: 1px dashed #e5e7eb;
        padding-bottom: 10px;
        margin-bottom: 10px;
      "
    >
      <span style="font-size: 22px; font-weight: 700; color: #111">设置</span>
    </div>

    <div class="settings-section-title" style="margin: 0 0 12px 4px">
      AI 引擎与提示词
    </div>
    <div class="form-group">
      <label class="form-label">API Base URL</label>
      <input
        type="text"
        v-model="config.baseUrl"
        class="form-input"
        placeholder="https://api.deepseek.com/v1"
      />
    </div>
    <div class="form-group">
      <label class="form-label">API Key</label>
      <div style="position: relative">
        <input
          :type="showApiKey ? 'text' : 'password'"
          v-model="config.apiKey"
          class="form-input"
          style="padding-right: 60px"
        />
        <button
          @click="showApiKey = !showApiKey"
          style="
            position: absolute;
            right: 10px;
            top: 50%;
            transform: translateY(-50%);
            background: none;
            border: none;
            cursor: pointer;
            color: var(--accent-color);
            font-size: 12px;
            font-weight: 700;
          "
        >
          {{ showApiKey ? '隐藏' : '显示' }}
        </button>
      </div>
    </div>
    <div class="form-group">
      <label class="form-label">Model Name</label>
      <input
        type="text"
        v-model="config.modelName"
        class="form-input"
      />
    </div>
    <div class="form-group">
      <div
        style="
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-bottom: 8px;
        "
      >
        <label class="form-label" style="margin: 0">System Prompt</label>
        <button class="badge-refresh" @click="resetPrompt">重置</button>
      </div>
      <textarea
        v-model="config.prompt"
        class="form-input"
        style="min-height: 120px"
      ></textarea>
      <button
        class="action-btn"
        style="
          display: block;
          margin: 10px auto 0;
          padding: 6px 14px;
          font-size: 12px;
          background: #f3f4f6;
          color: var(--text-secondary);
          border: 1px solid #e5e7eb;
          border-radius: 6px;
        "
        @click="testConnection"
      >
        {{ isTesting ? '测试中...' : '测试 API 连接' }}
      </button>
    </div>

    <div class="settings-section-title" style="margin: 24px 0 12px 4px">
      阅读与行为偏好
    </div>
    <div class="form-group">
      <label class="form-label">阅读字体</label>
      <div class="list-row-group">
        <div
          class="list-row-item"
          :class="{ active: config.fontFamily === 'sans-serif' }"
          @click="config.fontFamily = 'sans-serif'"
        >
          <span style="font-weight: 700; font-size: 15px; color: #111"
            >现代黑体</span
          >
          <span class="row-check">✓</span>
        </div>
        <div
          class="list-row-item"
          :class="{ active: config.fontFamily === 'serif' }"
          @click="config.fontFamily = 'serif'"
        >
          <span
            style="
              font-weight: 700;
              font-size: 15px;
              color: #111;
              font-family:
                &quot;Songti SC&quot;, &quot;Noto Serif CJK SC&quot;,
                &quot;SimSun&quot;, serif;
            "
            >经典宋体</span
          >
          <span class="row-check">✓</span>
        </div>
        <div
          class="list-row-item"
          :class="{ active: config.fontFamily === 'custom' }"
          @click="config.fontFamily = 'custom'"
        >
          <div
            style="
              display: flex;
              align-items: center;
              gap: 12px;
              width: 100%;
            "
          >
            <span
              style="
                font-weight: 700;
                font-size: 15px;
                color: #111;
                font-family: &quot;UserCustomFont&quot;, sans-serif;
              "
              >外部导入</span
            >
            <span
              style="
                font-size: 12px;
                color: #9ca3af;
                flex: 1;
                white-space: nowrap;
                overflow: hidden;
                text-overflow: ellipsis;
              "
            >
              {{ config.customFontName ? config.customFontName : '未导入字体' }}
            </span>
            <button
              class="badge-attachment import-font-btn"
              style="margin: 0"
              @click.stop="importCustomFont"
            >
              {{ config.customFontName ? '更换' : '导入' }}
            </button>
            <span class="row-check" style="margin-left: 4px">✓</span>
          </div>
        </div>
      </div>
    </div>

    <div class="form-group">
      <label class="form-label">追踪模式</label>
      <div class="segmented-control">
        <div
          class="segment-item"
          :class="{ active: config.trackMode === 'today' }"
          @click="config.trackMode = 'today'"
        >
          当日追踪
        </div>
        <div
          class="segment-item"
          :class="{ active: config.trackMode === 'continuous' }"
          @click="config.trackMode = 'continuous'"
        >
          持续追踪
        </div>
      </div>
      <div
        style="
          font-size: 11px;
          color: #9ca3af;
          text-align: center;
          margin-top: 8px;
        "
      >
        {{ config.trackMode === 'today' ? '仅追踪今日新发布条目' :
        '自动追踪历史遗漏条目' }}
      </div>
    </div>

    <div
      class="form-group"
      style="padding-top: 16px; border-top: 1px dashed #e5e7eb"
    >
      <div
        style="
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-bottom: 8px;
        "
      >
        <div>
          <div style="font-weight: 700; font-size: 14px; color: #111">
            后台巡检频率
          </div>
          <div style="font-size: 11px; color: #6b7280">
            自动检查新公文的基准间隔
          </div>
        </div>
        <div
          style="
            font-size: 14px;
            font-weight: 800;
            color: var(--accent-color);
          "
        >
          {{ config.pollingInterval || 30 }} 分钟
        </div>
      </div>
      <input
        type="range"
        min="15"
        max="240"
        step="15"
        v-model.number="config.pollingInterval"
        style="
          width: 100%;
          accent-color: var(--accent-color);
          cursor: pointer;
        "
      />
      <div
        style="
          display: flex;
          justify-content: space-between;
          font-size: 11px;
          color: #9ca3af;
          margin-top: 6px;
          font-weight: 500;
        "
      >
        <span>15分钟</span>
        <span>240分钟</span>
      </div>
    </div>

    <div
      style="
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding-top: 16px;
        border-top: 1px dashed #e5e7eb;
      "
    >
      <div style="font-weight: 700; font-size: 14px; color: #111">
        开机自启动
      </div>
      <input
        type="checkbox"
        class="ios-toggle"
        v-model="config.autoStart"
      />
    </div>

    <div
      style="
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding-top: 16px;
        margin-top: 16px;
        border-top: 1px dashed #e5e7eb;
      "
    >
      <div style="font-weight: 700; font-size: 14px; color: #111">
        免打扰模式
      </div>
      <input
        type="checkbox"
        class="ios-toggle"
        v-model="config.muteMode"
      />
    </div>

    <div
      style="
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-top: 24px;
        margin-bottom: 12px;
        padding-top: 24px;
        border-top: 1px solid rgba(0, 0, 0, 0.05);
      "
    >
      <div class="settings-section-title" style="margin: 0 0 0 4px">
        数据订阅管理
      </div>
      <div style="display: flex; gap: 8px">
        <span class="badge-attachment" @click="selectAllSources">全选</span>
        <span class="badge-clear" @click="deselectAllSources">清空</span>
      </div>
    </div>

    <div
      style="
        display: flex;
        flex-direction: column;
        gap: 10px;
        margin-bottom: 24px;
      "
    >
      <label
        v-for="(source, index) in allAvailableSources"
        :key="source"
        class="subs-chip"
        style="
          display: flex;
          justify-content: space-between;
          align-items: center;
          padding: 12px 16px;
        "
        :class="(config.subscribedSources || []).includes(source) ? 'active-morandi-' + (index % 8) : ''"
      >
        <span style="font-size: 14px">{{ source }}</span>
        <input
          type="checkbox"
          :value="source"
          v-model="config.subscribedSources"
        />
        <span
          v-show="(config.subscribedSources || []).includes(source)"
          style="font-weight: 900; opacity: 0.8; font-size: 15px"
          >✓</span
        >
      </label>
    </div>

    <div v-if="settingsMsg" class="floating-toast">
      {{ settingsMsg }}
    </div>
  </div>
</template>

<script setup>
// TODO: 添加组件逻辑
</script>

<style scoped>
/* 专属内联设置卡片样式 */
.inline-settings-card {
  cursor: default !important;
  padding: 24px 20px !important;
  /* 入场动画：与消息卡片一致 */
  animation: cardPopIn 0.4s var(--curve-pop, cubic-bezier(0.34, 1.56, 0.64, 1));
}

/* 入场动画关键帧 */
@keyframes cardPopIn {
  from {
    opacity: 0;
    transform: translateY(20px) scale(0.96);
  }
  to {
    opacity: 1;
    transform: translateY(0) scale(1);
  }
}

.inline-settings-card:hover,
.inline-settings-card:active {
  transform: none !important;
  background-color: var(--card-bg) !important;
}

/* 设置区标题 */
.settings-section-title {
  font-size: 12.5px;
  font-weight: 700;
  color: #6b7280;
  margin: 16px 0 8px 4px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

/* 设置卡片 */
.settings-card {
  background: #ffffff;
  border-radius: 12px;
  padding: 20px;
  border: 1px solid rgba(0, 0, 0, 0.05);
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.02);
  margin-bottom: 20px;
}

/* 高级输入框 */
.form-input {
  background-color: #f9fafb;
  border: 1px solid #e5e7eb;
  transition: all 0.25s ease;
}

.form-input:hover {
  background-color: #ffffff;
  border-color: #d1d5db;
}

.form-input:focus {
  background-color: #ffffff;
  border-color: var(--accent-color);
  box-shadow: 0 0 0 4px rgba(52, 120, 246, 0.15) !important;
}

/* 隐藏原生 Radio */
.custom-radio input[type="radio"] {
  display: none;
}

.custom-radio {
  position: relative;
  overflow: hidden;
}

/* 选中状态右上角小角标 */
.custom-radio.active::after {
  content: "✓";
  position: absolute;
  top: -2px;
  right: 6px;
  color: var(--accent-color);
  font-weight: 900;
  font-size: 16px;
}

/* iOS 风格 Toggle 开关 */
.ios-toggle {
  position: relative;
  width: 44px;
  height: 24px;
  appearance: none;
  -webkit-appearance: none;
  background: #e5e7eb;
  border-radius: 24px;
  outline: none;
  cursor: pointer;
  transition: background 0.3s;
}

.ios-toggle::after {
  content: "";
  position: absolute;
  top: 2px;
  left: 2px;
  width: 20px;
  height: 20px;
  background: white;
  border-radius: 50%;
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.2);
  transition: transform 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}

.ios-toggle:checked {
  background: var(--accent-color);
}

.ios-toggle:checked::after {
  transform: translateX(20px);
}

/* iOS 风格行列表 (用于字体选择) */
.list-row-group {
  border: 1px solid #e5e7eb;
  border-radius: 10px;
  overflow: hidden;
  background: #ffffff;
}

.list-row-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 16px;
  cursor: pointer;
  transition: background 0.2s;
  border-bottom: 1px solid #f3f4f6;
}

.list-row-item:last-child {
  border-bottom: none;
}

.list-row-item:hover {
  background: #f9fafb;
}

.list-row-item:active {
  background: #f3f4f6;
}

/* 原生风格单选勾号 */
.row-check {
  color: transparent;
  font-weight: 900;
  font-size: 16px;
  transition: color 0.2s;
}

.list-row-item.active .row-check {
  color: var(--accent-color);
}

/* 分段控制器 (用于追踪模式) */
.segmented-control {
  display: flex;
  background: #f3f4f6;
  padding: 3px;
  border-radius: 8px;
}

.segment-item {
  flex: 1;
  text-align: center;
  padding: 8px 0;
  font-size: 13px;
  font-weight: 600;
  color: #6b7280;
  cursor: pointer;
  border-radius: 6px;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
}

.segment-item.active {
  background: #ffffff;
  color: #111827;
  box-shadow:
    0 1px 3px rgba(0, 0, 0, 0.08),
    0 1px 2px rgba(0, 0, 0, 0.04);
}

/* 独立悬浮的 Toast 提示语 */
.floating-toast {
  position: fixed;
  bottom: 70%;
  left: 50%;
  transform: translateX(-50%);
  text-align: center;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(17, 24, 39, 0.85);
  backdrop-filter: blur(8px);
  color: #ffffff;
  padding: 10px 24px;
  border-radius: 20px;
  font-size: 13px;
  font-weight: 600;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
  z-index: 9999;
  pointer-events: none;
  width: max-content;
  max-width: 90%;
  animation: toastFadeIn 0.3s cubic-bezier(0.2, 0.8, 0.2, 1);
}

@keyframes toastFadeIn {
  from {
    opacity: 0;
    transform: translate(-50%, 10px);
  }
  to {
    opacity: 1;
    transform: translate(-50%, 0);
  }
}

/* 胶囊式多选按钮 (订阅管理) */
.subs-chip {
  padding: 8px 14px;
  border-radius: 20px;
  font-size: 13px;
  font-weight: 600;
  background: #f3f4f6;
  color: #4b5563;
  border: 1px solid transparent;
  cursor: pointer;
  transition: all 0.2s;
  user-select: none;
}

.subs-chip:hover {
  background: #e5e7eb;
}

.subs-chip:active {
  transform: scale(0.95);
}

.subs-chip input[type="checkbox"] {
  display: none;
}

/* 8种莫兰迪配色选中态 */
.subs-chip.active-morandi-0 {
  background-color: #e8d4d4;
  color: #8b6b6b;
  box-shadow: 0 2px 6px rgba(139, 107, 107, 0.15);
}

.subs-chip.active-morandi-1 {
  background-color: #d4e0e8;
  color: #5b7a8c;
  box-shadow: 0 2px 6px rgba(91, 122, 140, 0.15);
}

.subs-chip.active-morandi-2 {
  background-color: #d4e4d8;
  color: #5b8066;
  box-shadow: 0 2px 6px rgba(91, 128, 102, 0.15);
}

.subs-chip.active-morandi-3 {
  background-color: #e0d8e8;
  color: #7b6b8c;
  box-shadow: 0 2px 6px rgba(123, 107, 140, 0.15);
}

.subs-chip.active-morandi-4 {
  background-color: #f0e8d8;
  color: #8b7b5b;
  box-shadow: 0 2px 6px rgba(139, 123, 91, 0.15);
}

.subs-chip.active-morandi-5 {
  background-color: #e8dcc8;
  color: #8b7a5b;
  box-shadow: 0 2px 6px rgba(139, 122, 91, 0.15);
}

.subs-chip.active-morandi-6 {
  background-color: #d8e0e0;
  color: #6b8080;
  box-shadow: 0 2px 6px rgba(107, 128, 128, 0.15);
}

.subs-chip.active-morandi-7 {
  background-color: #e4dcd4;
  color: #7b6b5b;
  box-shadow: 0 2px 6px rgba(123, 107, 91, 0.15);
}
</style>

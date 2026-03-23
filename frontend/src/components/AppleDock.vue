<template>
  <!-- 🌟 底部功能栏：亚克力磨砂质感 -->
  <div
    class="main-card-header"
    :class="{ 'is-searching': isSearchUIExpanded }"
  >
    <div class="header-side left">
      <div
        class="search-morph-container"
        :class="{ 'is-active': isSearchUIExpanded }"
      >
        <input
          type="text"
          class="search-input"
          placeholder="Search Everything..."
          ref="searchInputRef"
          v-model="searchQuery"
          @input="handleSearch"
          @keyup.esc="closeSearchUI"
        />
        <div class="search-icon-wrapper" @click="handleSearchClick">
          <svg
            class="inline-search-icon"
            viewBox="0 0 1024 1024"
            xmlns="http://www.w3.org/2000/svg"
          >
            <path
              d="M1009.0112 935.936l-274.944-275.456a409.6 409.6 0 1 0-72.192 72.704l274.944 274.944a51.2 51.2 0 0 0 72.192-72.192z m-816.128-307.2A307.2 307.2 0 1 1 409.9712 716.8a307.2 307.2 0 0 1-217.088-89.088z"
              fill="currentColor"
            ></path>
          </svg>
        </div>
      </div>
    </div>

    <div class="header-center">
      <div
        class="dock-group"
        :class="{ 'is-collapsed': isSearchUIExpanded }"
      >
        <button
          class="dock-item today-btn"
          :class="{ 'is-active': activeSource === '全部' && !isSettingsOpen }"
          @click="handleTodayClick"
          data-tooltip="今日 / 退出搜索"
        >
          <svg
            viewBox="0 0 16.7871 20.9473"
            xmlns="http://www.w3.org/2000/svg"
          >
            <g fill="currentColor">
              <path
                d="M0 17.8809C0 19.9219 1.00586 20.9375 3.02734 20.9375L13.3984 20.9375C15.4199 20.9375 16.4258 19.9219 16.4258 17.8809L16.4258 3.06641C16.4258 1.03516 15.4199 0 13.3984 0L3.02734 0C1.00586 0 0 1.03516 0 3.06641ZM1.57227 17.8516L1.57227 3.0957C1.57227 2.11914 2.08984 1.57227 3.10547 1.57227L13.3203 1.57227C14.3359 1.57227 14.8535 2.11914 14.8535 3.0957L14.8535 17.8516C14.8535 18.8281 14.3359 19.3652 13.3203 19.3652L3.10547 19.3652C2.08984 19.3652 1.57227 18.8281 1.57227 17.8516Z"
              />
              <path
                d="M4.46289 5.01953L12.334 5.01953C12.6855 5.01953 12.9395 4.75586 12.9395 4.41406C12.9395 4.08203 12.6855 3.81836 12.334 3.81836L4.46289 3.81836C4.10156 3.81836 3.84766 4.08203 3.84766 4.41406C3.84766 4.75586 4.10156 5.01953 4.46289 5.01953ZM4.46289 7.85156L9.08203 7.85156C9.42383 7.85156 9.6875 7.58789 9.6875 7.24609C9.6875 6.91406 9.42383 6.65039 9.08203 6.65039L4.46289 6.65039C4.10156 6.65039 3.84766 6.91406 3.84766 7.24609C3.84766 7.58789 4.10156 7.85156 4.46289 7.85156ZM4.60938 17.5195L11.8262 17.5195C12.6855 17.5195 13.125 17.0801 13.125 16.2207L13.125 10.791C13.125 9.93164 12.6855 9.48242 11.8262 9.48242L4.60938 9.48242C3.7793 9.48242 3.30078 9.93164 3.30078 10.791L3.30078 16.2207C3.30078 17.0801 3.7793 17.5195 4.60938 17.5195Z"
              />
            </g>
          </svg>
        </button>

        <button
          class="dock-item hide-on-search"
          :class="{ 'is-active': activeSource === '收藏' && !isSettingsOpen }"
          @click="filterBySource('收藏')"
          data-tooltip="收藏夹"
        >
          <svg
            viewBox="0 0 22.0527 22.1191"
            xmlns="http://www.w3.org/2000/svg"
          >
            <path
              d="M4.16109 20.5469C4.56149 20.8594 5.0693 20.752 5.67477 20.3125L10.8408 16.5137L16.0166 20.3125C16.622 20.752 17.1201 20.8594 17.5302 20.5469C17.9306 20.2441 18.0185 19.7461 17.7744 19.0332L15.7334 12.959L20.9482 9.20898C21.5537 8.7793 21.7978 8.33008 21.6416 7.8418C21.4853 7.37305 21.0263 7.13867 20.2744 7.14844L13.8779 7.1875L11.9345 1.08398C11.7002 0.361328 11.3486 0 10.8408 0C10.3427 0 9.99117 0.361328 9.7568 1.08398L7.81344 7.1875L1.41695 7.14844C0.665001 7.13867 0.206017 7.37305 0.0497668 7.8418C-0.116249 8.33008 0.137657 8.7793 0.743126 9.20898L5.95797 12.959L3.91695 19.0332C3.67281 19.7461 3.7607 20.2441 4.16109 20.5469ZM5.56734 18.6133C5.54781 18.5938 5.55758 18.584 5.56734 18.5254L7.5107 12.9395C7.64742 12.5586 7.5693 12.2559 7.2275 12.0215L2.36422 8.66211C2.31539 8.63281 2.30563 8.61328 2.31539 8.58398C2.32516 8.55469 2.34469 8.55469 2.40328 8.55469L8.31149 8.66211C8.71188 8.67188 8.96578 8.50586 9.09274 8.10547L10.792 2.45117C10.8017 2.39258 10.8213 2.37305 10.8408 2.37305C10.8701 2.37305 10.8896 2.39258 10.8994 2.45117L12.5986 8.10547C12.7255 8.50586 12.9795 8.67188 13.3798 8.66211L19.288 8.55469C19.3466 8.55469 19.3662 8.55469 19.3759 8.58398C19.3857 8.61328 19.3662 8.63281 19.3271 8.66211L14.4638 12.0215C14.122 12.2559 14.0439 12.5586 14.1806 12.9395L16.124 18.5254C16.1338 18.584 16.1435 18.5938 16.124 18.6133C16.1045 18.6426 16.0752 18.623 16.0361 18.5938L11.3388 15.0098C11.0263 14.7656 10.665 14.7656 10.3525 15.0098L5.65524 18.5938C5.61617 18.623 5.58688 18.6426 5.56734 18.6133Z"
              fill="currentColor"
            />
          </svg>
        </button>

        <button
          class="dock-item hide-on-search"
          :class="{ 'is-active': isSettingsOpen }"
          @click="openSettings"
          data-tooltip="设置"
        >
          <svg
            viewBox="0 0 21.9531 21.3011"
            xmlns="http://www.w3.org/2000/svg"
          >
            <path
              d="M10.791 19.9816C11.0352 19.9816 11.2695 19.9718 11.5039 19.9523L12.041 20.9582C12.168 21.2121 12.3926 21.3097 12.666 21.2804C12.9492 21.2316 13.1152 21.0461 13.1445 20.7726L13.3105 19.6398C13.7793 19.5129 14.2188 19.3468 14.6484 19.1515L15.4883 19.9132C15.6934 20.1086 15.9473 20.1183 16.1914 19.9914C16.4355 19.8546 16.5332 19.6203 16.4746 19.3468L16.2305 18.2336C16.6211 17.9601 16.9824 17.6476 17.3242 17.3254L18.3691 17.755C18.6328 17.8527 18.877 17.7941 19.0625 17.5793C19.248 17.3644 19.2676 17.1203 19.1113 16.8761L18.5059 15.9191C18.7695 15.5187 19.0039 15.1086 19.2188 14.6886L20.3418 14.7277C20.625 14.7375 20.8398 14.6007 20.9375 14.3273C21.0352 14.0734 20.9668 13.8293 20.7422 13.6632L19.834 12.9699C19.9609 12.5109 20.0391 12.0421 20.0879 11.5539L21.1621 11.2121C21.4355 11.1242 21.5918 10.9289 21.5918 10.6457C21.5918 10.3625 21.4355 10.1574 21.1621 10.0793L20.0879 9.73746C20.0391 9.24918 19.9609 8.78043 19.834 8.32145L20.7324 7.62809C20.9668 7.46207 21.0254 7.21793 20.9375 6.95426C20.8398 6.68082 20.625 6.55387 20.3418 6.56363L19.2188 6.59293C19.0039 6.17301 18.7695 5.75309 18.5059 5.36246L19.1016 4.4152C19.2578 4.17105 19.2383 3.91715 19.0723 3.7023C18.877 3.48746 18.6328 3.43863 18.3789 3.53629L17.3242 3.96598C16.9824 3.62418 16.6113 3.32145 16.2305 3.04801L16.4746 1.94449C16.5332 1.66129 16.4355 1.43668 16.2012 1.29996C15.9473 1.15348 15.6934 1.19254 15.4883 1.37809L14.6387 2.13004C14.2188 1.93473 13.7695 1.77848 13.3008 1.64176L13.1445 0.518711C13.1152 0.245273 12.9395 0.0694922 12.6758 0.0108985C12.3926-0.0379296 12.168 0.0792579 12.041 0.323398L11.5039 1.32926C11.2695 1.31949 11.0352 1.30973 10.791 1.30973C10.5566 1.30973 10.3223 1.31949 10.0781 1.32926L9.55078 0.333164C9.41406 0.0792579 9.18945-0.028164 8.92578 0.0108985C8.64258 0.0597266 8.47656 0.245273 8.4375 0.518711L8.27148 1.64176C7.8125 1.77848 7.36328 1.94449 6.93359 2.1398L6.10352 1.37809C5.89844 1.18277 5.64453 1.16324 5.39062 1.29996C5.14648 1.43668 5.05859 1.67105 5.11719 1.94449L5.35156 3.05777C4.96094 3.33121 4.59961 3.63395 4.25781 3.96598L3.21289 3.53629C2.95898 3.42887 2.70508 3.49723 2.51953 3.71207C2.33398 3.91715 2.32422 4.17105 2.48047 4.4152L3.08594 5.37223C2.8125 5.76285 2.57812 6.18277 2.37305 6.6027L1.25 6.56363C0.957031 6.5441 0.751953 6.69059 0.654297 6.95426C0.546875 7.21793 0.625 7.46207 0.849609 7.62809L1.74805 8.32145C1.63086 8.78043 1.54297 9.24918 1.50391 9.73746L0.419922 10.0793C0.146484 10.1574 0 10.3625 0 10.6457C0 10.9289 0.146484 11.1242 0.419922 11.2121L1.50391 11.5539C1.54297 12.0421 1.63086 12.5109 1.74805 12.9699L0.849609 13.6632C0.625 13.8293 0.566406 14.0734 0.644531 14.3371C0.751953 14.6105 0.957031 14.7375 1.24023 14.7277L2.37305 14.6886C2.57812 15.1183 2.8125 15.5285 3.08594 15.9289L2.48047 16.8761C2.32422 17.1203 2.35352 17.3742 2.51953 17.5793C2.71484 17.8039 2.95898 17.8527 3.21289 17.755L4.25781 17.3156C4.59961 17.6574 4.9707 17.9601 5.35156 18.2433L5.11719 19.3468C5.05859 19.6203 5.15625 19.8546 5.39062 19.9914C5.64453 20.1379 5.89844 20.1086 6.09375 19.9132L6.94336 19.1515C7.37305 19.3566 7.82227 19.5129 8.28125 19.6398L8.4375 20.7629C8.47656 21.0461 8.65234 21.2218 8.91602 21.2804C9.19922 21.3293 9.41406 21.2121 9.55078 20.9582L10.0781 19.9523C10.3223 19.9718 10.5566 19.9816 10.791 19.9816ZM10.791 18.3605C6.5332 18.3605 3.07617 14.9035 3.07617 10.6457C3.07617 6.37809 6.5332 2.93082 10.791 2.93082C15.0586 2.93082 18.5059 6.37809 18.5059 10.6457C18.5059 14.9035 15.0586 18.3605 10.791 18.3605ZM9.0918 9.15152L10.2441 8.4191L6.98242 2.8234L5.78125 3.49723ZM12.9688 11.3097L19.4629 11.3097L19.4629 9.96207L12.9688 9.96207ZM10.2637 12.9015L9.12109 12.1593L5.68359 17.7648L6.875 18.4679ZM10.8008 13.2043C12.2168 13.2043 13.3594 12.0617 13.3594 10.6457C13.3594 9.22965 12.2168 8.08707 10.8008 8.08707C9.38477 8.08707 8.24219 9.22965 8.24219 10.6457C8.24219 12.0617 9.38477 13.2043 10.8008 13.2043ZM10.8008 11.7394C10.1953 11.7394 9.70703 11.2511 9.70703 10.6457C9.70703 10.0402 10.1953 9.55191 10.8008 9.55191C11.4062 9.55191 11.8945 10.0402 11.8945 10.6457C11.8945 11.2511 11.4062 11.7394 10.8008 11.7394Z"
              fill="currentColor"
            />
          </svg>
        </button>
      </div>
    </div>

    <div class="header-side right">
      <div class="right-actions">
        <button
          class="btn-check"
          :class="{
            loading: isLoading,
            'read-only-btn': isReadOnlyMode && !isLoading,
            'is-cooldown': isUpdateDisabled && !isLoading && !isReadOnlyMode
          }"
          @click="handleButtonClick()"
          :disabled="isUpdateDisabled && !isLoading && !isReadOnlyMode"
          data-tooltip="更新公文"
        >
          <span v-if="isReadOnlyMode" class="btn-text">只读</span>
          <span v-else-if="isLoading" class="btn-text"></span>
          <span v-else-if="isUpdateDisabled" class="btn-text"
            >{{ updateCooldown }}s</span
          >

          <svg
            v-else
            viewBox="0 0 24.0651 19.9316"
            xmlns="http://www.w3.org/2000/svg"
            style="width: 20px; height: 20px; flex-shrink: 0"
          >
            <g fill="currentColor">
              <path
                d="M0.694677 8.07617C-0.00844799 8.07617-0.184229 8.55469 0.19663 9.10156L2.38413 12.207C2.7064 12.6562 3.17515 12.6465 3.48765 12.207L5.66538 9.0918C6.04624 8.55469 5.86069 8.07617 5.1771 8.07617ZM21.8177 9.96094C21.8177 4.46289 17.3548 0 11.8568 0C6.35874 0 1.90561 4.45312 1.89585 9.9707C1.90561 10.4297 2.26694 10.791 2.71616 10.791C3.17515 10.791 3.55601 10.4199 3.55601 9.96094C3.55601 5.37109 7.26694 1.66016 11.8568 1.66016C16.4466 1.66016 20.1576 5.37109 20.1576 9.96094C20.1576 14.5508 16.4466 18.2617 11.8568 18.2617C9.07358 18.2617 6.62241 16.8945 5.13804 14.8242C4.8353 14.4238 4.37632 14.2969 3.96616 14.541C3.57554 14.7852 3.45835 15.3418 3.79038 15.7715C5.60679 18.291 8.50718 19.9219 11.8568 19.9219C17.3548 19.9219 21.8177 15.459 21.8177 9.96094Z"
              />
              <path
                d="M11.8568 4.24805C11.4076 4.24805 11.0462 4.59961 11.0462 5.04883L11.0462 10.5664C11.0462 10.8008 11.1244 10.9961 11.2904 11.2207L13.6537 14.3359C13.9955 14.7852 14.474 14.8535 14.8841 14.5703C15.2455 14.3164 15.2845 13.8281 14.9818 13.4082L11.7689 9.0625L12.6576 11.7871L12.6576 5.04883C12.6576 4.59961 12.2962 4.24805 11.8568 4.24805Z"
              />
            </g>
          </svg>
          <div class="aurora-light"></div>
          <div class="aurora-inner"></div>
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
// TODO: 添加组件逻辑
</script>

<style scoped>
/* ============================================================
   🌟 灵动岛 Header 卡片 - 详细配置说明
   ============================================================

   【整体架构】
   ┌─────────────────────────────────────────────────────────┐
   │  main-card-header (外层容器)                             │
   │  ┌──────────┐ ┌───────────────┐ ┌──────────┐            │
   │  │ 搜索框   │ │ dock-group    │ │ 更新按钮 │            │
   │  │ (左对齐) │ │ (今日/收藏/设置)│ │ (右对齐) │            │
   │  └──────────┘ └───────────────┘ └──────────┘            │
   └─────────────────────────────────────────────────────────┘

   【图标来源】
   所有图标均为【内联 SVG 自绘】，不依赖外部图标库。
   使用 stroke 描边风格，通过 currentColor 继承文字颜色。
   参考：Feather Icons 设计风格，简洁现代。

   【颜色变量说明】
   --pill-bg:      胶囊背景色 #f3f4f6 (极浅灰)
   --pill-hover:   悬浮背景色 #e5e7eb (浅灰)
   --btn-bg:       按钮背景色 #e5e7eb (与悬浮色一致)
   --curve-silky:  丝滑弹性曲线 cubic-bezier(0.22, 0.85, 0.15, 1)
   ============================================================ */

/* 统一背景色与悬浮色 */
:root {
  --pill-bg: rgba(0, 0, 0, 0.06);
  --pill-hover: rgba(0, 0, 0, 0.1);
  --btn-bg: rgba(0, 0, 0, 0.06);
  --curve-silky: cubic-bezier(0.22, 0.85, 0.15, 1);
}

/* Apple 级高透玻璃材质 */
.main-card-header {
  background: rgba(255, 255, 255, 0.15);
  backdrop-filter: blur(20px) saturate(180%);
  -webkit-backdrop-filter: blur(20px) saturate(180%);
  border-radius: 30px;
  padding: 6px 8px;
  box-shadow:
    0 16px 40px rgba(0, 0, 0, 0.08),
    0 4px 12px rgba(0, 0, 0, 0.04),
    inset 0 1px 1px rgba(255, 255, 255, 0.4);
  border: 1px solid rgba(255, 255, 255, 0.3);
  display: flex;
  align-items: center;
  justify-content: space-between;
  position: fixed;
  bottom: 16px;
  left: 16px;
  right: 16px;
  z-index: 100;
  max-width: 600px;
  margin: 0 auto;
  overflow: hidden;
  min-width: 0;
  box-sizing: border-box;
}

/* 左、右护法容器 */
.header-side {
  flex: 1;
  display: flex;
  align-items: center;
  min-width: 0;
  overflow: hidden;
  transition: flex 1s var(--curve-silky);
}

.header-side.left {
  justify-content: flex-start;
}

.header-side.right {
  justify-content: flex-end;
  z-index: 2;
}

.header-center {
  flex: 0 0 auto;
  display: flex;
  justify-content: center;
  margin: 0 8px;
  min-width: 0;
  z-index: 1;
  transition: margin 1s var(--curve-silky);
}

/* 搜索激活时：左侧护法疯狂膨胀 */
.main-card-header.is-searching .header-side.left {
  flex: 100;
  transition: flex 1s var(--curve-silky) 0.1s;
}

/* 搜索框撞击 Today 按钮 */
.main-card-header.is-searching .header-center {
  margin: 0 4px;
  animation: center-squish 1s var(--curve-silky) forwards;
}

@keyframes center-squish {
  0%,
  35% {
    transform: scale(1) translateX(0);
  }
  50% {
    transform: scaleX(0.85) scaleY(1.05) translateX(4px);
  }
  75% {
    transform: scaleX(1.02) scaleY(0.98) translateX(-2px);
  }
  100% {
    transform: scale(1) translateX(0);
  }
}

/* 力量传递给最右侧更新按钮 */
.main-card-header.is-searching .header-side.right {
  flex: 0 0 auto;
  animation: right-side-squish 1s var(--curve-silky) forwards;
}

@keyframes right-side-squish {
  0%,
  45% {
    transform: scale(1) translateX(0);
  }
  60% {
    transform: scaleX(0.88) scaleY(1.05) translateX(4px);
  }
  80% {
    transform: scaleX(1.02) scaleY(0.98) translateX(-2px);
  }
  100% {
    transform: scale(1) translateX(0);
  }
}

/* 搜索收起时的链式恢复动画 */
.main-card-header:not(.is-searching) .header-center {
  animation: center-bounce 0.6s var(--curve-silky) forwards;
}

@keyframes center-bounce {
  0% {
    transform: scale(1);
  }
  50% {
    transform: scale(0.96);
  }
  100% {
    transform: scale(1);
  }
}

.main-card-header:not(.is-searching) .header-side.right {
  animation: right-side-bounce 0.6s var(--curve-silky) forwards;
}

@keyframes right-side-bounce {
  0% {
    transform: scale(1);
  }
  60% {
    transform: scale(0.96);
  }
  100% {
    transform: scale(1);
  }
}

/* 响应式布局：小屏幕适配 */
@media (max-width: 470px) {
  .main-card-header {
    padding: 4px;
  }
  .header-center {
    margin: 0 4px;
  }
  .dock-item {
    width: 40px;
    height: 34px;
  }
  .btn-check {
    width: 38px !important;
    height: 38px !important;
  }
  .search-morph-container {
    height: 38px;
    width: 38px;
  }
  .search-morph-container.is-active {
    min-width: 0;
  }
  .btn-check:disabled,
  .btn-check.read-only-btn {
    width: 56px !important;
    height: 38px !important;
    font-size: 11px !important;
  }
}

/* 搜索框同频弹性引擎 */
.search-morph-container {
  display: flex;
  align-items: center;
  height: 44px;
  background: var(--btn-bg);
  border-radius: 22px;
  overflow: hidden;
  position: relative;
  width: 44px;
  flex-grow: 0;
  transition:
    width 1s var(--curve-silky),
    flex-grow 1s var(--curve-silky),
    background 0.6s ease;
}

.search-morph-container.is-active {
  width: 100%;
  flex-grow: 1;
  background: var(--pill-bg);
  animation: search-expand 0.8s var(--curve-silky) forwards;
}

@keyframes search-expand {
  0% {
    width: 44px;
  }
  100% {
    width: 100%;
  }
}

.search-morph-container:not(.is-active) {
  animation: search-collapse 0.6s var(--curve-silky) forwards;
}

@keyframes search-collapse {
  0% {
    width: 100%;
  }
  100% {
    width: 44px;
  }
}

/* dock-group 图标组容器 */
.dock-group {
  display: flex;
  align-items: center;
  background: var(--pill-bg);
  border-radius: 30px;
  padding: 2px;
  gap: 4px;
  overflow: hidden;
  position: relative;
  z-index: 1;
  will-change: max-width, background;
  transition:
    max-width 0.8s var(--curve-silky),
    background 0.8s var(--curve-silky),
    padding 0.8s var(--curve-silky);
}

/* 单个图标按钮 dock-item */
.dock-item {
  width: 50px;
  height: 38px;
  flex-shrink: 0;
  border-radius: 19px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  background: transparent;
  border: none;
  color: var(--text-main);
  position: relative;
  will-change: transform, background, width, margin;
  transition:
    transform 0.5s var(--curve-silky),
    opacity 0.4s ease,
    background 0.3s ease,
    box-shadow 0.4s var(--curve-pop),
    width 0.8s var(--curve-silky),
    margin 0.8s var(--curve-silky);
}

.dock-item svg {
  width: 20px;
  height: 20px;
  color: inherit;
}

/* 悬浮项：轻微放大 + 背景高亮 */
.dock-item:hover {
  transform: scale(1.1);
  background: var(--pill-hover);
  z-index: 2;
}

/* 点击动画 */
.dock-item:active {
  transform: scale(0.95) !important;
  transition: transform 0.1s ease;
}

/* 滑块背景效果 - 选中状态 */
.dock-item.is-active {
  background: rgba(0, 0, 0, 0.1);
  box-shadow:
    inset 0 1px 2px rgba(255, 255, 255, 0.5),
    0 1px 4px rgba(0, 0, 0, 0.08);
}

/* 搜索展开时隐藏滑块 */
.dock-group.is-collapsed .dock-item.is-active {
  background: transparent;
  box-shadow: none;
}

/* 搜索激活态：dock-group.is-collapsed */
.dock-group.is-collapsed {
  background: transparent;
  padding: 0;
  gap: 0;
  pointer-events: none;
  animation: dock-collapse 0.5s var(--curve-silky) forwards;
}

@keyframes dock-collapse {
  0% {
    gap: 10%;
    padding: 2px;
  }
  100% {
    gap: 0;
    padding: 0;
  }
}

.dock-group.is-collapsed .dock-item.hide-on-search {
  width: 0;
  opacity: 0;
  margin: 0;
  padding: 0;
  transform: translateX(-20px);
  pointer-events: none;
  transition:
    width 0.4s var(--curve-silky),
    opacity 0.3s ease,
    margin 0.4s ease,
    transform 0.4s var(--curve-silky),
    padding 0.4s ease;
}

.dock-group.is-collapsed .dock-item.today-btn {
  width: 44px !important;
  height: 44px !important;
  border-radius: 50% !important;
  background: var(--btn-bg);
  transform: scale(1) !important;
  opacity: 1;
  pointer-events: auto;
  flex-shrink: 0;
  transition: all 0.5s var(--curve-silky) 0.15s;
}

.dock-group.is-collapsed .dock-item.today-btn:active {
  transform: scale(0.9) !important;
}

/* 恢复动画：平滑展开 */
.dock-group:not(.is-collapsed) {
  animation: dock-expand 0.6s var(--curve-silky) forwards;
}

@keyframes dock-expand {
  0% {
    gap: 0;
    padding: 0;
  }
  100% {
    gap: 10%;
    padding: 2px;
  }
}

/* 搜索输入框 */
.search-input {
  flex: 1;
  height: 100%;
  border: none;
  background: transparent;
  outline: none;
  font-size: 14px;
  font-weight: 600;
  color: var(--text-main);
  padding: 0 44px 0 16px;
  min-width: 0;
  opacity: 0;
  transform: translateX(10px);
  transition:
    opacity 0.4s ease,
    transform 0.8s var(--curve-silky);
}

.search-morph-container.is-active .search-input {
  opacity: 1;
  transform: translateX(0);
}

.search-input::placeholder {
  color: #9ca3af;
  font-weight: 500;
}

/* 搜索图标按钮 */
.search-icon-wrapper {
  position: absolute;
  right: 0;
  width: 44px;
  height: 44px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  flex-shrink: 0;
  background: transparent;
  transition:
    background 0.4s ease,
    transform 0.2s ease;
  color: var(--text-main);
}

.search-morph-container.is-active .search-icon-wrapper {
  background: var(--btn-bg);
  margin: 4px;
  width: 36px;
  height: 36px;
}

.search-morph-container.is-active .search-icon-wrapper:active {
  transform: scale(0.9);
}

.search-icon-wrapper svg {
  width: 18px;
  height: 18px;
  stroke-width: 2.2px;
}

/* 内联搜索图标样式 */
.inline-search-icon {
  width: 18px;
  height: 18px;
  flex-shrink: 0;
}

/* 右侧按钮容器 */
.right-actions {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  position: relative;
  z-index: 10;
}

/* 更新按钮 */
.btn-check {
  width: 44px;
  height: 44px;
  border-radius: 50%;
  background: var(--btn-bg);
  color: var(--text-main);
  display: flex;
  align-items: center;
  justify-content: center;
  border: none;
  cursor: pointer;
  flex-shrink: 0;
  transition: all 0.8s var(--curve-silky);
  position: relative;
  z-index: 3;
  overflow: hidden;
}

.btn-check:hover {
  background: var(--pill-hover);
  transform: scale(1.05);
}

.btn-check:active {
  transform: scale(0.95);
}

/* 冷却/只读状态 */
.btn-check:disabled,
.btn-check.read-only-btn {
  width: 44px !important;
  height: 44px !important;
  border-radius: 50% !important;
  background: var(--pill-bg) !important;
  color: #9ca3af !important;
  font-size: 12px !important;
  font-weight: 600;
  border: none !important;
  cursor: not-allowed;
  letter-spacing: -0.5px;
  transition: all 0.4s var(--curve-silky) !important;
}

.main-card-header.is-searching .btn-check:not(:disabled) {
  transform: scale(0.96);
  border-radius: 50% !important;
}

/* CSS 弹性 Tooltip */
.dock-item::after,
.btn-check::after {
  content: attr(data-tooltip);
  position: absolute;
  top: calc(100% + 10px);
  left: 50%;
  transform: translateX(-50%) scale(0.8);
  opacity: 0;
  visibility: hidden;
  background: rgba(17, 24, 39, 0.85);
  backdrop-filter: blur(8px);
  color: #ffffff;
  font-size: 12px;
  font-weight: 600;
  padding: 6px 12px;
  border-radius: 8px;
  white-space: nowrap;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
  pointer-events: none;
  z-index: 100;
  transform-origin: top center;
  transition: all 0.2s ease;
  transition-delay: 0s;
}

.dock-item::before,
.btn-check::before {
  content: "";
  position: absolute;
  top: 100%;
  left: 50%;
  transform: translateX(-50%);
  opacity: 0;
  visibility: hidden;
  border: 5px solid transparent;
  border-bottom-color: rgba(17, 24, 39, 0.85);
  pointer-events: none;
  z-index: 100;
  transition: all 0.2s ease;
  transition-delay: 0s;
}

.dock-item:hover::after,
.btn-check:hover::after {
  opacity: 1;
  visibility: visible;
  transform: translateX(-50%) scale(1);
  transition: all 0.4s cubic-bezier(0.34, 1.56, 0.64, 1);
  transition-delay: 0.1s;
}

.dock-item:hover::before,
.btn-check:hover::before {
  opacity: 1;
  visibility: visible;
  transition: all 0.4s ease;
  transition-delay: 0.1s;
}

/* 加载状态：变圆并在极光中心 */
.btn-check.loading {
  width: 38px !important;
  height: 38px !important;
  border-radius: 50% !important;
  background: transparent !important;
  color: transparent !important;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.06) !important;
  transform: none !important;
}

/* 彩虹极光动画 */
.aurora-light {
  position: absolute;
  inset: -4px;
  border-radius: 50%;
  background: conic-gradient(
    from 0deg,
    #ffb3ba,
    #ffffba,
    #baffc9,
    #bae1ff,
    #d5baff,
    #ffb3ba
  );
  filter: blur(5px);
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.5s ease;
}

.btn-check.loading .aurora-light {
  opacity: 0.7;
  animation: aurora-spin 4s linear infinite;
}

.aurora-inner {
  position: absolute;
  inset: 3px;
  background: #ffffff;
  border-radius: 50%;
  z-index: 1;
  opacity: 0;
  transition: opacity 0.5s ease;
}

.btn-check.loading .aurora-inner {
  opacity: 1;
}

@keyframes aurora-spin {
  0% {
    transform: rotate(0deg) translateZ(0);
  }
  100% {
    transform: rotate(360deg) translateZ(0);
  }
}

/* 外部 SVG 专属遮罩渲染类 */
.svg-icon-mask {
  width: 20px;
  height: 20px;
  background-color: var(--text-main);
  display: block;
  -webkit-mask-size: contain;
  -webkit-mask-repeat: no-repeat;
  -webkit-mask-position: center;
  mask-size: contain;
  mask-repeat: no-repeat;
  mask-position: center;
}

.svg-icon-mask.sm {
  width: 18px;
  height: 18px;
  z-index: 2;
}
</style>

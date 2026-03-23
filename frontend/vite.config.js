import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'path'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [vue()],

  // 路径别名配置
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src'),
    },
  },

  // 开发服务器配置
  server: {
    port: 5173,
    // 代理配置（如需要后端 API 代理）
    proxy: {
      // 示例：将 /api 请求代理到后端
      // '/api': {
      //   target: 'http://localhost:8000',
      //   changeOrigin: true,
      // },
    },
  },

  // 构建配置
  build: {
    outDir: 'dist',
    sourcemap: false,
    // 静态资源处理
    assetsDir: 'assets',
    // chunk 大小警告限制
    chunkSizeWarningLimit: 1500,
    rollupOptions: {
      output: {
        // 分包策略
        manualChunks: {
          vue: ['vue'],
        },
      },
    },
  },

  // CSS 配置
  css: {
    // CSS 预处理器配置
    preprocessorOptions: {
      // scss: {
      //   additionalData: `@import "@/styles/variables.scss";`
      // }
    },
  },
})

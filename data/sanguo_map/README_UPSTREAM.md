# 三国势力地图 🗺️

> **公元 160—220 年** 东汉末年至三国鼎立，年度势力版图交互式可视化

一个基于 **MapLibre GL JS** 的高性能 WebGL 地图应用，以"古风水墨"美学呈现三国时代 61 年间各方势力的领土变迁。

---

## ✨ 功能特性

| 功能 | 说明 |
|------|------|
| 🎯 **四层缩放层级** | 州 → 郡 → 县，逐级展开行政区划 |
| ⏳ **时间轴控件** | 160—220 年逐年拖动，一键播放动画（1 秒/年） |
| 🎨 **势力着色** | 70+ 势力独立配色，古风墨韵调色板 |
| 📊 **势力图例** | 实时统计各势力控制郡数，按权重排序 |
| 📌 **信息面板** | 点击任意郡弹出详情（郡名 / 年份 / 所属势力） |
| 🏔️ **自然地理** | 长江、黄河等主要河流 + 湖泊水系渲染 |
| 🖌️ **古风美学** | 暗色宣纸底图 + 金色虚线州界 + 暗角光效 |

---

## 🏗️ 技术栈

```
sanguo-map/                 ← pnpm Monorepo
├── packages/api/           ← Fastify REST API (GeoJSON + 势力数据)
│   ├── data/               ← 预处理后的 JSON/GeoJSON 数据
│   └── src/                ← 路由、服务器
├── packages/web/           ← React + Vite 前端
│   ├── src/components/     ← MapView, TimelineSlider, Legend, InfoPanel
│   ├── src/hooks/          ← useMapData (数据获取 + 全年预加载缓存)
│   └── src/styles/         ← 全局古风 CSS
└── package.json            ← Monorepo 根配置
```

- **前端**: React 18 + TypeScript + Vite 5 + MapLibre GL JS
- **后端**: Fastify 4 + TypeScript + Turf.js (空间运算)
- **包管理**: pnpm Workspaces

---

## 🚀 快速开始

### 前置要求

- [Node.js](https://nodejs.org/) ≥ 18
- [pnpm](https://pnpm.io/) ≥ 8

### 安装依赖

```bash
# 解压后进入项目目录
cd sanguo-map

# 安装所有依赖（首次运行需要联网）
pnpm install
```

> ⚠️ **如果没有安装 pnpm**，请先运行：`npm install -g pnpm`

### 数据准备（可选）

项目已包含预处理后的数据文件（`packages/api/data/` 目录），**开箱即用无需重新生成**。

如果你修改了原始数据（`raw-data/` 目录下的 GeoJSON 或 Excel 文件），需要重新生成：

```bash
pnpm prepare-data
```

### 一键启动开发环境

```bash
pnpm dev
```

这会同时启动：
- **API 服务**: http://localhost:3001
- **Web 前端**: http://localhost:5173

打开浏览器访问 `http://localhost:5173` 即可。

### 单独启动

```bash
# 仅启动 API
pnpm dev:api

# 仅启动前端
pnpm dev:web
```

### 生产构建

```bash
pnpm build
```

---

## 📁 API 端点

| 路由 | 说明 |
|------|------|
| `GET /health` | 健康检查 |
| `GET /geo/commanderies` | 郡级 GeoJSON (Polygon) |
| `GET /geo/states` | 州级 GeoJSON (Polygon, 由郡合并生成) |
| `GET /geo/counties` | 县级 GeoJSON (Point/Polygon) |
| `GET /geo/rivers` | 河流 GeoJSON (LineString) |
| `GET /geo/lakes` | 湖泊 GeoJSON (Polygon) |
| `GET /factions?year=190` | 指定年份的势力归属 JSON (160-220) |
| `GET /factions/colors` | 势力配色方案 JSON |
| `GET /factions/years` | 可用年份列表 JSON |

---

## 🎮 操作指南

1. **拖动时间轴**：底部滑块 160—220 年，实时更新地图着色
2. **点击播放按钮**：自动逐年推进动画
3. **点击刻度标注**：快速跳转到历史关键节点（黄巾起义、官渡之战、赤壁之战等）
4. **鼠标悬停郡**：底部 Tooltip 显示郡名 + 势力
5. **点击郡**：右上角弹出信息面板
6. **缩放地图**：滚轮缩放，低 zoom 看州级、中 zoom 看郡级、高 zoom 看县级
7. **左上角图例**：查看当前年份所有势力及其控制郡数

---

## 📜 License

MIT

# clipper agent

> 本文档用于指导后端代码编写。
> - API 基于 **FastAPI** 实现，工作流使用 **LangGraph**，视频处理依赖 **ffmpeg/ffprobe**。
> - 前后端分离，前端通过 **HTTP + SSE** 与后端交互。

## 目录

1. [基础信息](#1-基础信息)
2. [后端流程总览](#2-后端流程总览)
3. [数据模型](#3-数据模型json-schema)
4. [API 接口列表](#4-api-接口列表)
5. [状态流转图](#5-状态流转图)
6. [错误码汇总](#6-错误码汇总)
7. [实现提示](#7-实现提示针对后端开发)
8. [LLM 模型配置](#8-llm-模型配置)

---

## 1. 基础信息

### 1.1 Base URL

- 开发环境默认：`http://127.0.0.1:8848`
- 可通过环境变量修改：
  - `CLIPPER_UI_HOST`（默认 `127.0.0.1`）
  - `CLIPPER_UI_PORT`（默认 `8848`）

### 1.2 鉴权

当前版本无鉴权。后续可扩展 API Key 或 JWT。

### 1.3 CORS

默认不启用 CORS。设置环境变量 `CLIPPER_CORS_ORIGINS`（逗号分隔）后启用，例如：

```bash
export CLIPPER_CORS_ORIGINS="http://127.0.0.1:5173,http://localhost:5173"
```

### 1.4 数据目录结构

后端运行时会在工作目录（cwd）下创建以下目录：

- `CLIPPER_UPLOADS_DIR`（默认 `out/uploads`）：存放原始上传视频
- `CLIPPER_RUNS_DIR`（默认 `out/runs`）：存放每个 run 的工作目录，结构如下：

```
out/runs/
  {run_id}/
    metadata.json          # 工作流状态、高光列表、转场等
    segments/              # 切分后的 5 分钟片段（可选，可即时删除）
    audio_transcript.csv   # 语音转文字结果（合并后）
    vision_description.csv # 视频理解结果（合并后）
    highlight_clips/       # 根据高光切片出的临时片段
    highlight_reel.mp4     # 最终生成的高光合集
```

### 1.5 并发限制

后端同时执行的分析任务（`/analyze`）和渲染任务（`/render`）总数限制为 **5**（可配置）。超出时返回 `429 Too Many Requests`。

---

## 2. 后端流程总览

整体分为两个阶段，与第 4 节 API 的对应关系如下：

### 第一阶段：高光选取与理解

| # | 步骤 | 说明 | 对应接口 |
|---|------|------|----------|
| 1 | 获取视频 | 前端上传或后端下载一段长视频 + 元数据 | `POST /api/upload` |
| 2 | 创建工作流 | `ffprobe` 读取时长、分辨率等元数据 | `POST /api/runs` |
| 3 | 切分 | `clip(time_length=5min, file=video.mp4) -> file_list` | `/analyze` 后台任务 |
| 4 | 并发分析 | 对每个片段执行：ASR（语音转文字）+ VLM（视频理解），输出带时间轴 CSV | `/analyze` 后台任务 |
| 5 | 合并 | 脚本朴素实现：`merge(audio_result_list)` / `merge(video_result_list)`，按时间排序 | `/analyze` 后台任务 |
| 6 | 生成高光 | LLM 结合用户提示词（段数、主题、偏好）挑选高光时刻 `highlight_time = List[start_sec, end_sec]` | `POST /api/runs/{run_id}/highlights` |
| 7 | 前端交互 | 允许修改提示词重新生成（回到第 6 步），也允许直接修改 `highlight_time`，后端同步保存 | `PUT /api/runs/{run_id}/highlights` |

### 第二阶段：视频生成

| # | 步骤 | 说明 | 对应接口 |
|---|------|------|----------|
| 1 | 切片 | `clip_highlight(file, highlight_time) -> highlight_file_list` | `/render` 后台任务 |
| 2 | 选择转场 | 用户选择 / 自带模板 / agent 选择或联网搜索 | `GET /api/transitions` |
| 3 | 拼接 | `merge(highlight_file_list, transition)` 输出最终视频 | `POST /api/runs/{run_id}/render` |

---

## 3. 数据模型（JSON Schema）

### 3.1 HighlightSegment

```json
{
  "start_sec": 12.3,
  "end_sec": 25.6,
  "reason": "主角大笑，观众反应强烈"
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `start_sec` | number | 是 | 起始时间（秒） |
| `end_sec` | number | 是 | 结束时间（秒），必须 `> start_sec` |
| `reason` | string | 否 | 高光原因（由 LLM 生成） |

### 3.2 RunStatus 枚举

| 状态 | 含义 |
|------|------|
| `created` | 实例已创建，未开始分析 |
| `analyzing` | 正在执行音视频分析（切分 + ASR + VLM） |
| `analysis_done` | 分析完成，等待生成高光 |
| `highlights_generated` | 高光时段已生成（可手动修改） |
| `rendering` | 正在渲染最终视频 |
| `done` | 全部完成（最终视频可用） |
| `error` | 发生不可恢复的错误 |
| `cancelled` | 用户取消 |

> 说明：`metadata.json` 中保存当前 `status`、`highlights`、`transition` 等运行时状态。

### 3.3 ArtifactPaths

```json
{
  "segments_dir": "out/runs/{run_id}/segments/",
  "audio_csv": "out/runs/{run_id}/audio_transcript.csv",
  "vision_csv": "out/runs/{run_id}/vision_description.csv",
  "highlight_clips_dir": "out/runs/{run_id}/highlight_clips/",
  "final_video": "out/runs/{run_id}/highlight_reel.mp4"
}
```

---

## 4. API 接口列表

接口总览：

| 方法 | 路径 | 说明 | 同步/异步 |
|------|------|------|-----------|
| GET | `/healthz` | 健康检查 | 同步 |
| POST | `/api/upload` | 上传视频 | 同步 |
| POST | `/api/runs` | 创建工作流实例 | 同步 |
| POST | `/api/runs/{run_id}/analyze` | 执行音视频分析 | 异步（202） |
| POST | `/api/runs/{run_id}/highlights` | 生成高光时段 | 同步 |
| PUT | `/api/runs/{run_id}/highlights` | 手动修改高光时段 | 同步 |
| GET | `/api/transitions` | 获取可用转场列表 | 同步 |
| POST | `/api/runs/{run_id}/render` | 渲染最终视频 | 异步（202） |
| POST | `/api/runs/{run_id}/cancel` | 取消运行中的任务 | 同步 |
| GET | `/api/runs/{run_id}/stream` | 订阅工作流事件（SSE） | 流式 |
| GET | `/api/runs/{run_id}` | 查询状态与产物元信息 | 同步 |
| GET | `/api/runs/{run_id}/artifacts/{filename}` | 下载产物文件 | 同步 |
| DELETE | `/api/runs/{run_id}` | 删除工作流 | 同步 |

---

### 4.1 健康检查

**GET** `/healthz`

响应：

```json
{ "status": "ok" }
```

---

### 4.2 上传视频

**POST** `/api/upload`

- Content-Type: `multipart/form-data`
- 字段：`file`（视频文件）

响应 `200 OK`：

```json
{
  "upload_id": "e8a69a2a-7f4b-4b06-9f7e-64a1d33b2e4a",
  "video_path": "out/uploads/e8a69a2a-7f4b-4b06-9f7e-64a1d33b2e4a.mp4"
}
```

错误：

- `400`：无文件或文件名非法
- `413`：文件过大（可配置，默认 2GB）

---

### 4.3 创建工作流实例

**POST** `/api/runs`

请求体：

```json
{
  "video_path": "out/uploads/xxx.mp4"
}
```

后端行为：

- 校验 `video_path` 存在且位于工作目录下
- 使用 `ffprobe` 读取时长、分辨率等元数据
- 创建 run 目录和 `metadata.json`
- 初始状态：`created`

响应 `201 Created`：

```json
{
  "run_id": "run_uuid",
  "video_path": "out/uploads/xxx.mp4",
  "duration_sec": 123.45,
  "status": "created"
}
```

错误：

- `400`：`video_path` 无效或不存在
- `409`：该 `video_path` 已有活跃 run（可选，允许重复创建）

---

### 4.4 执行音视频分析

**POST** `/api/runs/{run_id}/analyze`

- 异步任务，立即返回 `202 Accepted`
- 前置条件：状态必须为 `created` 或 `analysis_done`（若为 `analysis_done` 则重新分析并覆盖）

请求体（可选）：

```json
{
  "segment_duration_sec": 300
}
```

- `segment_duration_sec`：切片时长（秒），默认 `300`（5 分钟），范围 30～600

响应 `202 Accepted`：

```json
{
  "run_id": "run_uuid",
  "status": "analyzing"
}
```

错误：

- `404`：run_id 不存在
- `409`：当前状态不允许分析（如 `rendering` 或 `done`）
- `429`：超出并发限制（同时运行任务数 ≥ 5）

**后台任务细节**：

1. 将视频切分为 `segment_duration_sec` 的片段（使用 ffmpeg）
2. 对每个片段并发执行：
   - 语音转文字（ASR），输出包含 `start`、`end`、`content` 的 CSV（时间轴相对于原视频）
   - 视频理解（VLM），输出包含 `start`、`end`、`description` 的 CSV
3. 合并所有片段的 CSV 为两个总 CSV（按时间排序）
4. 状态更新为 `analysis_done`
5. 可选：删除原始片段文件以节省空间（保留 CSV 即可）

进度通过 SSE 推送（见 [4.10](#410-订阅工作流事件sse)）。

---

### 4.5 生成高光时段

**POST** `/api/runs/{run_id}/highlights`

- 实现方式：**同步**（LLM 推理通常在数秒内完成，且用户要求可多次调用；若未来耗时过长可改为异步）
- 前置条件：状态为 `analysis_done` 或 `highlights_generated`

请求体：

```json
{
  "prompt": "找出最搞笑和感人的片段",
  "num_segments": 5
}
```

- `prompt`：用户自由文本，后端提取关键需求（主题、偏好等）
- `num_segments`：期望的高光段数，默认 `5`，范围 1～20

响应 `200 OK`：

```json
{
  "highlights": [
    { "start_sec": 12.3, "end_sec": 25.6, "reason": "主角大笑，观众反应强烈" },
    { "start_sec": 100.5, "end_sec": 112.0 }
  ]
}
```

错误：

- `404`：run_id 不存在
- `409`：分析未完成（状态不是 `analysis_done` 或 `highlights_generated`）
- `422`：请求体格式错误

后端逻辑：

- 读取 `audio_transcript.csv` 和 `vision_description.csv`
- 构造 LLM prompt（包含用户输入、CSV 内容摘要或全文）
- 调用语言模型返回高光时段列表（JSON）
- 将结果存入 `metadata.json`，状态改为 `highlights_generated`
- 返回高光列表（前端展示后可手动调整）

---

### 4.6 手动修改高光时段

**PUT** `/api/runs/{run_id}/highlights`

- 覆盖后端存储的高光列表
- 前置条件：状态为 `highlights_generated` 或 `done`（若已渲染，修改后应重置渲染状态）

请求体：

```json
{
  "highlights": [
    { "start_sec": 15.0, "end_sec": 28.0, "reason": "用户手动调整" },
    { "start_sec": 100.5, "end_sec": 112.0 }
  ]
}
```

- `reason` 可选

响应 `200 OK`：同 [4.5](#45-生成高光时段) 的响应格式。

错误：

- `400`：高光时段重叠或时间超出视频时长
- `404`：run_id 不存在
- `409`：当前状态不允许修改（例如 `analyzing`）

后端行为：

- 校验每个片段 `start_sec < end_sec`，且不超出 `duration_sec`
- 可选：去重合并重叠片段
- 保存新列表；若当前状态为 `done`，则回退为 `highlights_generated`（修改后需要重新渲染）

---

### 4.7 获取可用转场效果列表

**GET** `/api/transitions`

响应 `200 OK`：

```json
{
  "transitions": ["fade", "wipe", "slide", "zoom", "none"]
}
```

- 列表由后端动态生成（可读取配置目录或插件系统）
- 每个转场名称对应 ffmpeg 的滤镜实现

---

### 4.8 渲染最终视频

**POST** `/api/runs/{run_id}/render`

- 异步任务，立即返回 `202 Accepted`
- 前置条件：状态为 `highlights_generated`（高光已确定）

请求体：

```json
{
  "transition": "fade"
}
```

- `transition` 必须是 `/api/transitions` 返回的某个值

响应 `202 Accepted`：

```json
{
  "run_id": "run_uuid",
  "status": "rendering"
}
```

错误：

- `404`：run_id 不存在
- `409`：当前状态不允许渲染（例如缺少高光列表）
- `422`：转场不支持
- `429`：超出并发限制

**后台任务细节**：

1. 根据当前 `highlights` 列表，使用 ffmpeg 从原视频切出片段（`highlight_clips/`）
2. 使用指定转场效果拼接所有片段（ffmpeg `filter_complex`，见 [7.5](#75-ffmpeg-调用建议)）
3. 输出 `highlight_reel.mp4`
4. 状态更新为 `done`
5. 删除临时片段（可选保留）

进度通过 SSE 推送（见 [4.10](#410-订阅工作流事件sse)）。

---

### 4.9 取消正在运行的任务

**POST** `/api/runs/{run_id}/cancel`

- 用于取消当前正在执行的异步任务（分析或渲染）
- 可重复调用，无任务时返回成功但无效果

响应 `200 OK`：

```json
{
  "run_id": "run_uuid",
  "previous_status": "analyzing",
  "status": "cancelled"
}
```

错误：

- `404`：run_id 不存在

实现要求：

- 后端任务应定期检查取消标志（如检查 `asyncio.Event` 或线程标志）
- 取消后清理临时文件，状态置为 `cancelled`
- 前端可通过 `GET /api/runs/{run_id}` 获取最新状态

---

### 4.10 订阅工作流事件（SSE）

**GET** `/api/runs/{run_id}/stream`

- 建立 SSE 连接，实时接收进度、日志、结果事件
- 断开后不保留历史事件（前端应配合 `GET /api/runs/{run_id}` 轮询状态）

响应头：

```
Content-Type: text/event-stream
Cache-Control: no-cache
Connection: keep-alive
```

**事件格式**：每条 `data: <json>\n\n`

**事件类型**：

`progress`

```json
{
  "type": "progress",
  "phase": "analyzing",      // "analyzing" | "rendering"
  "step": "ffmpeg split",    // 可选
  "percent": 20              // 0-100
}
```

`log`

```json
{
  "type": "log",
  "level": "info",           // "info" | "warning" | "error"
  "message": "Segment 3/10 processed"
}
```

`result`

```json
{
  "type": "result",
  "phase": "highlights",
  "data": { ... }            // 例如高光列表
}
```

`done`

```json
{
  "type": "done",
  "status": "done"           // 最终状态
}
```

`error`

```json
{
  "type": "error",
  "message": "FFmpeg failed"
}
```

实现提示：

- 使用 FastAPI 的 `StreamingResponse` + 异步生成器
- 为每个 run_id 维护一个事件队列（或使用 `asyncio.Queue`）
- 任务执行过程中将事件 `put` 到队列，SSE 生成器从队列读取

---

### 4.11 获取工作流状态与产物元信息

**GET** `/api/runs/{run_id}`

响应 `200 OK`：

```json
{
  "run_id": "uuid",
  "video_path": "out/uploads/xxx.mp4",
  "duration_sec": 123.45,
  "status": "done",
  "highlights": [ ... ],
  "transition": "fade",
  "artifacts": {
    "segments_dir": "out/runs/{run_id}/segments/",
    "audio_csv": "out/runs/{run_id}/audio_transcript.csv",
    "vision_csv": "out/runs/{run_id}/vision_description.csv",
    "highlight_clips_dir": "out/runs/{run_id}/highlight_clips/",
    "final_video": "out/runs/{run_id}/highlight_reel.mp4"
  },
  "errors": ["分析阶段：语音识别超时"]
}
```

- `artifacts` 中路径可能为空（如果尚未生成）
- `errors` 数组记录非致命错误或警告

错误：

- `404`：run_id 不存在

---

### 4.12 下载产物文件

**GET** `/api/runs/{run_id}/artifacts/{filename}`

- `filename`：相对于 run 目录的文件名（例如 `highlight_reel.mp4`、`audio_transcript.csv`）
- 安全限制：只允许字母数字、下划线、点、短横，禁止 `..` 路径穿越

响应：文件流（`FileResponse`），自动设置 `Content-Disposition` 为 `inline` 或 `attachment`（建议视频用 `inline`，CSV 用 `attachment`）。

错误：

- `404`：文件不存在
- `400`：非法文件名

---

### 4.13 删除工作流

**DELETE** `/api/runs/{run_id}`

- 终止正在运行的任务（若有）
- 删除整个 `out/runs/{run_id}` 目录及其所有内容
- 释放资源

响应 `204 No Content`（无响应体）

错误：

- `404`：run_id 不存在

---

## 5. 状态流转图

```
created
   │
   ▼ (POST /analyze)
analyzing ──(cancel)──> cancelled
   │
   ▼ (分析完成)
analysis_done
   │
   ▼ (POST /highlights)
highlights_generated ──(PUT /highlights)──> highlights_generated (可重复)
   │
   ▼ (POST /render)
rendering ──(cancel)──> cancelled
   │
   ▼ (渲染完成)
done
   │
   ▼ (DELETE /runs/{id})
(资源删除)
```

补充说明：

- 任何状态遇到不可恢复错误 → `error`
- 从 `done` 状态调用 `PUT /highlights` 后，状态回退为 `highlights_generated`

---

## 6. 错误码汇总

| 状态码 | 含义 | 适用场景 |
|--------|------|----------|
| 200 | OK | 同步请求成功 |
| 201 | Created | 创建工作流成功 |
| 202 | Accepted | 异步任务已开始 |
| 204 | No Content | 删除成功 |
| 400 | Bad Request | 参数校验失败、时间轴超出范围 |
| 404 | Not Found | run_id 或文件不存在 |
| 409 | Conflict | 状态不允许当前操作（例如未分析就生成高光） |
| 413 | Payload Too Large | 上传视频过大 |
| 422 | Unprocessable Entity | 请求体格式错误（如 JSON 校验失败） |
| 429 | Too Many Requests | 超出并发限制 |

统一错误响应格式（非 SSE）：

```json
{ "detail": "具体错误描述" }
```

---

## 7. 实现提示（针对后端开发）

### 7.1 LangGraph 集成建议

虽然 API 是分步的，但每个步骤内部可以使用 LangGraph 管理子流程。例如：

- 分析阶段：构建一个图，节点包括 `split_video`、`parallel_asr_vlm`、`merge_csv`
- 高光生成：可以用 LangGraph 调用 LLM 并校验输出
- 渲染：图节点包括 `clip_segments`、`apply_transition`、`concat`

这样便于复用和调试。

### 7.2 并发限制实现

使用 `asyncio.Semaphore(5)` 控制同时运行的分析 + 渲染任务总数。注意区分任务类型，可共用同一信号量。

### 7.3 取消任务实现

每个运行中的任务应接收一个 `cancel_event: asyncio.Event`。在耗时操作（如 ffmpeg 调用、循环切片）中定期检查 `cancel_event.is_set()`，若为 True 则终止并清理临时文件。

### 7.4 路径安全

所有用户提供的路径（`video_path`、`filename`）必须：

- 解析绝对路径后，检查是否位于 `CLIPPER_RUNS_DIR` 或 `CLIPPER_UPLOADS_DIR` 之下
- 使用 `pathlib.Path.resolve()` 防止软链接攻击

### 7.5 ffmpeg 调用建议

- 切片：

  ```
  ffmpeg -i input.mp4 -ss start -t duration -c copy segment_%03d.mp4
  ```

- 拼接带转场：使用 `filter_complex`，推荐 `xfade`（视频） + `acrossfade`（音频）实现片段间的真实转场，示例（fade，转场时长 1s）：

  ```
  ffmpeg -i clip1.mp4 -i clip2.mp4 -filter_complex \
    "[0:v][1:v]xfade=transition=fade:duration=1:offset=4[v]; \
     [0:a][1:a]acrossfade=d=1[a]" \
    -map "[v]" -map "[a]" -c:v libx264 -c:a aac output.mp4
  ```

  > 注意：`xfade` 要求参与拼接的片段分辨率、帧率一致；`offset` 约为「第一个片段时长 - 转场时长」。建议封装为独立函数，按转场类型生成对应的 `filter_complex`，支持多种转场。

### 7.6 存储清理策略

- 提供 `DELETE /api/runs/{run_id}` 供前端主动清理
- 同时实现后台定时任务（如每 24 小时）删除超过 7 天的 run 目录

---

## 8. LLM 模型配置

| 用途 | 默认模型 | 文档 |
|------|----------|------|
| 视频理解（VLM） | `qwen3-vl-flash` | [阿里云百炼文档](https://bailian.console.aliyun.com/?tab=doc#/doc/?type=model&url=2845871) |
| 语音识别（ASR） | `Fun-ASR` | [阿里云百炼文档](https://bailian.console.aliyun.com/?tab=doc#/doc/?type=model&url=2880903) |
| 语言模型（LLM） | `qwen3.5-flash` | [阿里云百炼文档](https://bailian.console.aliyun.com/?tab=doc#/doc/?type=model&url=2841718) |

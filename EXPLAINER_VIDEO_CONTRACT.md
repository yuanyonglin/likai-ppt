# 讲解视频功能 · 并行开发接口约定

本文件是并行分支（PPTX 解析 / 生成链路 / 导出适配 / 前端）共同遵守的约定。改约定需在汇报里说明，由合并方统一调整。
可点击原型：`../prototype-explainer-video/prototype.html`（仓库外，同级目录）；参考实现：`../prototype-explainer-video/make_video.py`。

## 1. 视频页的表示（所有分支依赖）

视频页是一条**普通幻灯片**（`SlideData` 行 + `Project.slides_data` 条目），不新增幻灯片类型：

- `html_content`：自包含的 1280×720 页面，根元素带标记属性，内容为封面图 + `<video>`：

  ```html
  <div class="explainer-video-page" data-explainer-video-id="{video_id}"
       style="width:1280px;height:720px;position:relative;background:#000;overflow:hidden">
    <video src="/api/explainer-videos/{video_id}/video.mp4" poster="/api/explainer-videos/{video_id}/cover.png"
           controls playsinline preload="metadata" style="width:100%;height:100%;object-fit:contain"></video>
  </div>
  ```

- **识别视频页的唯一依据**是 `html_content` 里的 `data-explainer-video-id` 属性（编辑器保存可能重写 metadata，HTML 属性不会丢）。同时在 `slide_metadata` 写 `{"kind": "explainer_video", "explainer_video_id": ...}` 作为便捷字段，但读取方必须以 HTML 属性为准。
- 公共判断函数（生成链路分支提供，其他分支先按同名实现临时版本，合并时以生成链路为准）：
  `landppt.services.explainer_video.page.parse_explainer_video_id(html: str) -> Optional[str]`
- 编辑器、放映、分享页直接渲染该 HTML 即可播放，无需单独播放逻辑。

## 2. 数据与文件（生成链路分支独占）

- 新表 `explainer_videos`：`id`(str uuid)、`user_id`、`project_id`(nullable)、`source_type`(`slide`|`upload`|`text`)、`source_ref`(JSON)、`title`、`status`、`error`、`script_json`、`video_path`、`cover_path`、`duration_ms`、`artifact_id`、`replaced_slide_backup`(JSON，替换时备份原页 `html_content`/`title`/`slide_metadata`)、`created_at`、`updated_at`。
- 文件端点（需在编辑器、**分享页（未登录 share token）**、Playwright 导出三处都能访问，参照 `/api/image/view/` 的访问控制做法）：
  - `GET /api/explainer-videos/{id}/video.mp4`
  - `GET /api/explainer-videos/{id}/cover.png`（封面帧 1920×1080）

## 3. HTTP 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/explainer-videos/parse-upload` | multipart `file`；返回 `{file_id, filename, sections:[{index, title, text}]}`。PPTX 按页、PDF 按页、Word/MD 按标题分节。 |
| POST | `/api/explainer-videos` | 创建生成任务，返回 `{video_id, task_id}` |
| GET | `/api/explainer-videos/{id}` | `{id, status, error, title, source_type, source_ref, script:{title, segments:[{text, visual, bullets, start, duration}]}, video_url, cover_url, duration_ms, placements:[{project_id, slide_id, mode}]}` |
| GET | `/api/explainer-videos?project_id=` | 列表（素材库也走 artifact 列表） |
| POST | `/api/explainer-videos/{id}/place` | `{project_id, after_slide_id?, replace_slide_id?}` 二选一；返回 `{slide_id, slide_index}` |
| POST | `/api/explainer-videos/{id}/restore` | `{project_id, slide_id}`：把被替换的页恢复为备份内容 |
| POST | `/api/explainer-videos/{id}/regenerate` | `{script?}`：可带改过的讲解稿重新配音/录制 |

创建请求体：

```json
{
  "source": {"type": "slide", "project_id": "...", "slide_id": "..."}
            | {"type": "upload", "file_id": "...", "section_index": 7}
            | {"type": "text", "title": "...", "text": "..."},
  "extra": "补充讲解方向（可空）",
  "options": {"duration": "1m|2m|3m", "tts_provider": "...", "voice": "...", "style": "follow_ppt|business_blue|dark_tech", "subtitles": true},
  "placement": {"mode": "insert|replace|none"}
}
```

- **定位页面一律用 `slide_id`**，不用页码。
- `tts_provider`/`voice` 由请求携带，选择逻辑照搬现有旁白生成（`web/route_modules/narration_routes.py` 的 `/narration/generate`），不得写死 Edge-TTS。
- 任务状态沿用现有后台任务机制与轮询端点（生成链路分支在汇报中写出具体端点）；状态值：`pending`→`running`(附 `stage`: `parse|script|tts|animation|render`)→`completed`|`failed`。
- `placement.mode=insert|replace` 时任务完成后自动执行 place。

## 4. 插页 / 替换必须走的现有入口

- 插入视频页：`DatabaseService.apply_slide_structure_operation`（`database/service.py`，`op_type="insert"`），它会调用 `_remap_page_documents` 同步讲稿与配音索引。
- 替换：更新该 `slide_id` 的 `html_content`/`slide_metadata`，原内容写入 `replaced_slide_backup`；同步删除该页讲稿与配音缓存。

## 5. 导出与讲稿（导出适配分支独占）

| 场景 | 视频页的表现 |
|---|---|
| PDF / 图片 | 封面帧 |
| PPTX | 嵌入可播放 MP4（参考 `tasks/handlers/export_tasks.py` 的 `add_movie`），需实测 PowerPoint/WPS |
| 讲稿生成、配音生成 | 跳过视频页 |
| 旁白视频导出 | 该页直接拼接视频页 MP4（带原声） |

## 6. 文件归属（每个文件只有一个分支修改）

| 分支 | 独占 |
|---|---|
| PPTX 解析 | `services/file_processor.py`、`frontend/src/components/source-materials.ts`、`pyproject.toml`、`uv.lock`、新的解析模块 |
| 生成链路 | `database/models.py`、`database/migrations.py`、`services/explainer_video/**`（新）、新路由及注册、任务处理新文件；`narration_service.py` 只调用不修改 |
| 导出适配 | `tasks/handlers/export_tasks.py`、`services/video_export_service.py`、`services/pyppeteer_pdf_converter.py`、`services/export_infra/**`、讲稿/配音“跳过视频页”的改动（`speech_script_service.py`、`narration_service.py` 中的过滤） |
| 前端 | `frontend/src/**`（除 `source-materials.ts`）、`web/static/js/pages/project/slides_editor/**` |

需要别人文件里的改动或新依赖：在汇报里写明，不要自己改。

## 7. 工作树环境

工作树里没有被忽略的文件：`.venv`、`frontend/node_modules`、`.env`、`landppt.db`、`uploads/`、`static/antd/`。
- `uv sync`；`cd frontend && npm ci`
- 需要起服务时复制一份 `landppt.db`（来自主目录 `/Users/jay/Desktop/project/LK/ai-ppt-lk/LandPPT/landppt.db`）和 `.env`，端口用 8000 以外的；不要提交 `.db`/`.env`。

# 第三方来源与使用范围

| 组件 | 官方来源 | 说明 |
|---|---|---|
| 前端参考结构 | [Dify webapp-text-generator](https://github.com/langgenius/webapp-text-generator) | 固定版本与 MIT 原文见 SOURCE.md / frontend/LICENSE |
| Dify 服务 | [langgenius/dify](https://github.com/langgenius/dify) | 独立部署依赖，遵守部署版本的 Dify Open Source License 及其附加条件 |
| Next.js / React | [vercel/next.js](https://github.com/vercel/next.js)、[facebook/react](https://github.com/facebook/react) | 依赖通过包管理器获取；以所安装版本的许可为准 |
| FastAPI / Pydantic / HTTPX / Uvicorn | 各包官方项目 | 固定 Python 依赖见 backend/requirements.txt |
| Qwen 权重与 tokenizer | [QwenLM](https://github.com/QwenLM)、官方 Hugging Face 组织 | 权重不包含在仓库；模型数据卡、固定 revision 与许可独立审核 |
| 中文研究数据 | docs/DATASETS.md 的作者仓库 | 数据不包含在仓库；代码许可与原始社交文本授权分别审查 |

前端参考项目的 MIT 许可不等同于 Dify 平台全部代码的 MIT 许可。依赖、模型和数据的版权声明及许可条件应在实际发布包、镜像与模型服务中保留。

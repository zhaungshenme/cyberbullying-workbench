# 数据源核验记录

核验日期：2026-10-10；只访问作者仓库元信息、README、LICENSE 和论文正文/摘要，不下载语料文本或平台个人资料。

| 作者仓库 | 固定 commit | 核验入口 | 结果 |
|---|---|---|---|
| thu-coai/COLDataset | a0c55a945497ab58e94aef491180e1cf88ffb864 | GitHub REST `/repos/thu-coai/COLDataset`、`/readme`、`/contents/LICENSE`、递归 tree；[原论文全文](https://arxiv.org/html/2201.06025) | Apache-2.0；37,480；知乎/微博；训练半自动、测试三人多数决；无上下文 |
| aggiejiang/SWSR | 7b320184da8f9dd6d366dfe726c6b3e12ac92f77 | GitHub REST `/repos/aggiejiang/SWSR`、`/readme`、`/contents/LICENSE`、递归 tree | MIT；1,527 原帖、8,969 评论；平台 ID 与用户属性包含在数据说明中 |
| DUT-lujunyu/ToxiCN | 91d8f6a449967bd310364544d535d6310e45043b | GitHub REST `/repos/DUT-lujunyu/ToxiCN`、`/readme`、递归 tree；[ACL 页面](https://aclanthology.org/2023.acl-long.898/) | README 明确 CC BY-NC-ND 4.0；仅科研、禁商业；GitHub license=null 不表示没有条款 |
| shenmeyemeifashengguo/STATE-ToxiCN | eea5cd92f94bdd1ded4b38b1d264246531baf714 | GitHub REST `/repos/shenmeyemeifashengguo/STATE-ToxiCN`、`/readme`、递归 tree；[ACL 页面](https://aclanthology.org/2025.findings-acl.532/) | README 明确仅科研、严格禁商业；未发现独立通用许可证 |

许可原文证据：

- ToxiCN：`This work is licensed under the Creative Commons Attribution-NonCommercial-NoDerivatives 4.0 International License (CC BY-NC-ND 4.0).` 以及 `All resources are intended solely for scientific research and are prohibited from commercial use.`
- STATE-ToxiCN：`All resources are for scientific research purposes only and are strictly prohibited from commercial use.`
- COLD 与 SWSR 独立 LICENSE 已读取；分别为 Apache License Version 2.0 与 `MIT License / Copyright (c) 2022 Aiqi Jiang`。

同目录 JSON 保存完整 README、许可证（存在时）、tree 文件路径、commit、官方 URL 和核验日期，可由发布维护者复查。论文一致性数值仅是作者论文报告值，没有将其作为本项目测试结果。

未核验项目：SWSR Zenodo API 因 DNS 解析失败无法访问；各平台原帖与每条原始授权本次未核验；语料条数未通过实际下载计数复查；没有现成业务金标或生产使用授权。因此文件规模称作者发布说明，商业许可判断以已读取条款和具体用途授权为准。

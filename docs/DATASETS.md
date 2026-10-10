# 中文语料来源、许可与金标建设

核验日期：2026-10-10。本文给出可执行的数据建设方案；列出的公开语料是候选资源，不代表本项目已经下载、训练或获得生产使用授权。本次只核验作者仓库、许可证、论文与文件路径，没有收集平台用户材料。

## 1. 选定的数据路线

以 **COLD 为通用中文攻击性语言基准、SWSR 为性别攻击与原帖语境补充**，优先完成来源及使用范围登记。ToxiCN、STATE-ToxiCN 用于许可允许范围内的科研对照；进入生产模型训练或产品前须另外取得覆盖该用途的书面授权。正式业务金标来自依法取得、明确允许本项目训练/评测的脱敏材料，保留事件上下文、标注证据及授权台账。

公开攻击性/仇恨言论标签并不等于司法上的网暴、违法或犯罪标签。任何公开数据都不能直接证明事件持续性、组织性、账号真实身份、个人信息真伪或损害结果。

执法辅助或非营利使用也不自动等于“科学研究”；对明确仅科研的资源，实际业务用途需取得覆盖该用途的授权。

| 数据源与作者正式发布 | 已核验规模/粒度 | 适合用途 | 许可与上线处理 |
|---|---|---|---|
| [COLD / 清华 CoAI 官方仓库](https://github.com/thu-coai/COLDataset)，[EMNLP 2022 论文](https://aclanthology.org/2022.emnlp-main.796/) | 37,480 条；训练/开发共 32,157、测试 5,323；句子级冒犯二分类，测试另有四类 | 通用攻击性语言基线、反偏见困难负例、跨模型可比评测 | 仓库 [Apache-2.0](https://github.com/thu-coai/COLDataset/blob/a0c55a945497ab58e94aef491180e1cf88ffb864/LICENSE)；保留许可/来源。代码或数据发布许可不能代替对原始社交文本、个人信息与具体用途的授权核验 |
| [SWSR / 作者 Aiqi Jiang 仓库](https://github.com/aggiejiang/SWSR)，[论文 DOI](https://doi.org/10.1016/j.osnem.2021.100182) | 1,527 条微博原帖、8,969 条评论；评论二分类、四种性别攻击类别、攻击对象类别；另有 3,016 词词表 | 性别攻击子集；用 weibo_id 关联原帖与评论、检查原帖语境 | 仓库 [MIT](https://github.com/aggiejiang/SWSR/blob/7b320184da8f9dd6d366dfe726c6b3e12ac92f77/LICENSE)。原文件含用户属性和平台 ID，工作副本最小化、脱敏；生产使用先登记数据权利与用途 |
| [ToxiCN / 大连理工 DUTIR 官方仓库](https://github.com/DUT-lujunyu/ToxiCN)，[ACL 2023 论文](https://aclanthology.org/2023.acl-long.898/) | 作者 README 称 12k 评论；毒性、毒性类型、表达方式、目标群体层次标注；来自知乎/贴吧 | 隐性攻击、转述、群体偏见的科研基准；须独立检查是否与其他来源重叠 | [README 许可声明](https://github.com/DUT-lujunyu/ToxiCN/blob/91d8f6a449967bd310364544d535d6310e45043b/README.md)：**CC BY-NC-ND 4.0、仅科研、禁止商业用途**。不纳入默认生产训练；改写、衍生数据再分发和生产使用需核验条款并另获授权 |
| [STATE-ToxiCN / 作者官方仓库](https://github.com/shenmeyemeifashengguo/STATE-ToxiCN)，[ACL 2025 Findings 论文](https://aclanthology.org/2025.findings-acl.532/) | 目标/论据 span 与毒性相关结构抽取；有 data/train.json、data/test.json | 可追溯证据片段、对象与攻击表达科研评测；不能直接当事件层网暴金标 | [README Ethics Statement](https://github.com/shenmeyemeifashengguo/STATE-ToxiCN/blob/eea5cd92f94bdd1ded4b38b1d264246531baf714/README.md)：**仅科研、严格禁止商业用途**；未发现独立通用许可证，不因 GitHub 可下载就取得无限制使用权 |

SWSR 作者还给出早期版本 [Zenodo DOI](https://doi.org/10.5281/zenodo.4773875)。本次未成功访问 Zenodo API，版本/许可不能用该链接替代已核验的 GitHub 固定版本。

## 2. 可重复的获取路径

先把数据目录设为服务器的受控私有目录，放在公开项目仓库之外。以下命令是后续执行路径，本次未执行数据下载。执行前完成对应来源许可和用途登记。固定 revision；不采用会变化的 main 作为训练版本。

```bash
# 新服务器由管理员为当前授权数据管理员建立目录；已有目录先核实属主。
sudo install -d -m 750 -o "$(id -un)" -g "$(id -gn)" /srv/workbench-data/vendor
cd /srv/workbench-data/vendor

git clone https://github.com/thu-coai/COLDataset.git cold
git -C cold checkout --detach a0c55a945497ab58e94aef491180e1cf88ffb864
# 文件：cold/COLDataset/{train,dev,test}.csv

git clone https://github.com/aggiejiang/SWSR.git swsr
git -C swsr checkout --detach 7b320184da8f9dd6d366dfe726c6b3e12ac92f77
# 文件：swsr/SWSR/SexWeibo.csv、SexComment.csv

find cold/COLDataset swsr/SWSR -type f -name '*.csv' -exec sha256sum '{}' \; \
  > dataset-sha256.txt
```

ToxiCN 固定版本 `91d8f6a449967bd310364544d535d6310e45043b`，原始文件 `ToxiCN_1.0.csv`；STATE-ToxiCN 固定版本 `eea5cd92f94bdd1ded4b38b1d264246531baf714`，文件 `data/train.json`、`data/test.json`。仅在许可适用的科研空间或额外授权覆盖的空间下载使用，不能混入上述默认生产数据目录。

文件获取后保存：仓库 URL、完整 commit、实际下载时间、文件 SHA256、原始相对路径、行/记录号、作者引用、许可文件快照、允许用途、审批/授权编号和到期撤回规则。作者后续更新不会覆盖历史训练版本。

## 3. 质量证据及适用边界

**COLD。** [论文正文](https://arxiv.org/html/2201.06025)明确原始来源为知乎与微博，通过关键词和相关话题抽样；训练语料采用模型在环的半自动标注及人工检查，不是全部逐条独立多人金标。测试集每条三人标注、多数决；论文报告测试二分类 Fleiss κ=0.819、四分类 κ=0.757。这些是一致性证据，不是本项目准确率。论文也明确未包含上下文；种族、性别、地域话题与关键词抽样不能代表平台自然分布。

**SWSR。** 性别攻击专项数据，不覆盖威胁、人肉曝光、群体围攻等所有类别。weibo_id 能证明该发布文件提供的原帖关联，不能补造评论回复链、评论者身份或完整事件。用户性别、地域、粉丝数等字段不作为研判输入，避免无关身份属性影响判断；只保留经审查必需的文本与结构。词表只用于采样与质量检查，命中词语本身不构成攻击。

**ToxiCN / STATE-ToxiCN。** 标注定义与当前业务协议不同；隐性攻击、报告表达、目标 span 的对照价值高，但许可限制明确。STATE-ToxiCN README 介绍按测试表现选择学习率的实验做法，本项目不照搬：超参数只在开发集选择，最终测试集锁定后只用于最终报告。

语料质量通过完整性核验、抽样复审、跨来源去重和独立金标评测逐步证明，不能因为论文发表或仓库公开就声称满足执法准确率。

## 4. 原标签到本项目标签的转换规则

保留 `source_label` 原值，生成另存的 `annotation_v1`；不覆盖作者标签。转换器先产生候选任务，人工补齐证据与语境后才生成监督训练目标。

| 原标签 | 原定义 | 项目中的使用方式 |
|---|---|---|
| COLD train/dev `label=0/1` | safe / offensive | 仅训练/评测原生二分类；不能自动变成 `not_supported/supported` 的完整证据结果 |
| COLD test `fine-grained-label=0,1,2,3` | other non-offensive / attack individual / attack group / anti-bias | 保留测试原生评测；1/2 是候选攻击，3 重点用于反偏见表达困难负例；补标当前发言行为、证据和对象后才能适配项目协议 |
| ToxiCN `toxic=0/1`、`toxic_type=0/1/2` | 非毒性/毒性；非毒性/一般冒犯/仇恨 | 保留科研标签，不把群体仇恨直接当犯罪分类 |
| ToxiCN `expression=0/1/2/3` | non-hate / explicit hate / implicit hate / reporting | reporting 是重要语境候选，必须核查是否夹带当前发言人的新攻击；不能直接标无攻击 |
| ToxiCN `target` | LGBTQ 0、Region 1、Sexism 2、Racism 3、Others 4、non-hate 5，列表 | 表示目标群体类别，不能写入需真实 `author_key` 的 `target_actor_key` |
| SWSR `label=0/1`，`category=SA/SCB/MA/SO`，`target=I/G` | 非性别歧视/性别歧视；外貌刻板印象/文化背景刻板印象/微攻击/性冒犯；个人/泛指群体 | 补充性别攻击与正常讨论子集；SO 只作 `sexualized_insult` 候选，经证据核对后确认；I/G 不代表具体账号 |
| STATE-ToxiCN 的目标/论据 span 等原结构 | 细粒度目标与有害表达关联 | 原始字段与论文定义单独保存；适配时重新校验片段为输入原文精确子串，不把目标短语当已验证账号 |

当前项目输出是 `decision` 三值（`supported/not_supported/insufficient_context`）、语言观察 `observations`、逐字 `evidence`、可空 `target_actor_key`、理由与缺失信息。`supported` 必须含已确认攻击标签和可验证原文；其余判断不能含攻击标签。不能仅凭原始二分类数字生成整段模型理由或伪造 gold evidence。

## 5. 接入已有材料接口

当前导入协议接受独立文本行或事件 `messages`。每个 COLD 单句单独作为一条事件，不凭相同 case_id 拼成对话；SWSR 可在确认原帖关联后保留原帖与评论，但评论之间不构造未知回复关系。缺失作者、时间、回复设为 null。

下面仅为**自编协议演示**，不是采集语料，不进入真实样本统计：

```json
{
  "case_id": "demo:synthetic:record-1",
  "task": "language_observation_decision",
  "focus_message_id": "m1",
  "messages": [
    {"message_id": "m1", "author_key": null, "reply_to": null,
     "timestamp": null, "text": "这项服务的说明不清楚，请给出退款处理时间。"}
  ]
}
```

导入器记录材料哈希、源记录编号和 case_id 等引用；它没有替代许可登记系统。数据治理字段应另存 `provenance.jsonl`，通过原始记录键关联。工作台单次上传当前最多 1 MiB、100 个窗口，批量训练语料应走独立离线数据管线，不能整库灌入一次网页请求。

建议 provenance 记录字段：`dataset_id/revision/file_sha256/source_file/source_record_id/source_label/acquisition_method/license_id/allowed_uses/authorization_id/redaction_version/parent_event_id/split/annotation_version`。缺失原文链接保留 `null`，明确“可追溯至作者发布记录，未核验平台原帖”；不生成看似真实的评论 URL。

## 6. 业务金标建设与评测落地

1. **合法来源落到具体渠道。** 首选合作平台的官方导出/API、单位依法取得且明确允许本次训练/评测的举报材料、经知情同意的研究参与者材料。公开网页可阅读不代表允许批量抓取和用于生产训练。未获授权的抖音、贴吧爬取不列入实施方案；控制请求频率不能代替授权。
2. **先做 2,000 条试标。** 来源去重、敏感信息最小化，覆盖攻击、引用、求助、投诉、讽刺、反驳、含混对象、谐音变体等；每条两名独立标注员，第三人裁决分歧。先完成指南版本与一致性检查，再扩建目标约 50,000 条训练候选、5,000 条开发、10,000 条最终评测；这是建设目标，现有数量不是这些值。
3. **按事件而非单条随机划分。** 同事件/同作者（可靠 ID 可用时）/高度相似改写不跨训练与测试。来源和时间分别留出；多平台各做外部测试。未知作者不能宣称实现作者隔离。来自公开资源的原测试集保留原用途；公开语料可能已进入基础模型预训练，最终业务测试还需采用独立、新时间段的授权材料，以减少数据泄漏造成的虚高表现。
4. **金标只由人确认。** 多模型用于建议、争议发现与主动学习抽样；匿名呈现模型结果或先盲标，降低锚定。模型一致不等于金标，合成数据独立标记为 `synthetic`、用于协议或鲁棒性测试，不混进真实质量分母。现有工作台复核记录保留审计，但当前还没有已实现的独立多人金标导出流水线。
5. **性能按风险分项。** 报告 supported 的 precision/recall、每类 macro-F1、正常投诉/求助/引用/反偏见的误报率、上下文不足识别、原文 span 精确匹配、对象误归因、无效输出率；同时给各来源/类别样本量和 bootstrap 置信区间。威胁、曝光、组织协调等高风险类别人工逐条复核。
6. **阈值在开发集定，测试只验收。** 自报置信度不能作概率；用独立校准及模型/规则输出共同确定转人工策略。通过业务方定义的误报/漏报验收门槛后再小范围部署；没有独立测试结果时不写准确率承诺。例如 3,000 条独立自然分布的正常表达中观察到 0 次误报，只能粗略说明该子集误报率的单侧 95% 上界约 0.1%，不能宣称零误报或覆盖所有司法场景。
7. **持续更新可回滚。** 原始授权副本只读；保留脱敏版本、标注变更、数据撤回/剔除列表、训练数据 manifest 和模型 checkpoint 关联。上线错例先进入复审队列，新模型必须通过旧冻结测试和新外部测试，人工结论不会自动覆写为训练金标。

以上计划把“来源可信”“标签可靠”“本场景有效”分别验证：作者公开资源建立可追溯起点，人工上下文金标建立业务可靠性，独立冻结评测建立可部署证据。

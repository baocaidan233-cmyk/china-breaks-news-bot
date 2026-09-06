# China Breaks（CCP-exposure）新闻频道 — 运行规则文档

最后更新：2026-09-04

## 一句话说明

这是把 **AM1ST（America First）** 目前最新、经过几周真实迭代打磨的 Python 架构，原样搬过来、换一个内容领域重新长出来的新项目：**China Breaks**，一个专门在 Gettr 上做「揭露中共」内容的英文频道。跟 AM1ST 一样，整个系统分成两个**完全独立、互不等待**的流程：一个负责抓新闻、去重、打分、生成文案、存起来；另一个每隔一段时间从存好的那批里挑一篇发出去。两个流程各自有自己的调度节奏，中间靠一个共享的 Notion 数据库（候选池）传递信息。

```
┌─────────────┐        写入候选池        ┌─────────────┐
│  抓取流程     │ ──────────────────────▶ │  候选池      │
│ main.py     │                          │ (Notion库)   │
│ 每10分钟一次  │                          └──────┬──────┘
└─────────────┘                                  │ 读取
                                                  ▼
                                          ┌─────────────┐
                                          │  发布流程     │
                                          │main_publish.py│
                                          │ 每30分钟一次  │
                                          └─────────────┘
```

**这个项目目前只完成了「代码搭好 + 推到 GitHub」这一步——还没有部署到任何 VM，也从没用真实凭证跑过一次。** 下面写的所有"确认过的真实信息"（Notion 库 id、Gettr 账号身份等）都只是作为参考记录在这份文档里，`.env` / `config.yaml` 里全是空值或占位符，没有写进任何一个真实凭证。

---

## 一、这个项目是怎么来的

- **架构来源**：AM1ST（`/Volumes/T7 Red/收集箱T2/Claude/News_Agents/AM1ST/`）是一个已经跑了几周、经历过大量真实迭代和修bug的 Python 新闻机器人（RSS抓取 → 去重 → 打分 → 全文提取 → 生成文案 → 发布到 Gettr）。China Breaks 是把 AM1ST **当前**这套完整机制（不只是核心抓取/发布循环，也包括事件身份LLM验证器、人名地名词典、Notion人工热点标记、共享无头浏览器渲染客户端、多key OpenAI容错）整体搬过来，只换掉跟"美国国内政治"绑死的部分。
- **内容领域来源**：China Breaks 本身其实**已经在用一套更早、目前仍在真实运行的 n8n workflow**（`/Volumes/T7 Red/收集箱T2/n8n/China Breaks/` 下的三个导出文件，最新的抓取版本是 `v4.0_chinabreaks_news_to_notion_branch2.json`，发布版本是 `v3.4_chinabreaks_notion_to_gettr.json`）。这个 Python 版本**是打算未来替换掉那套 n8n 系统的，但这次构建不涉及替换、不涉及部署、不会碰任何真实凭证**——纯粹是把代码写出来、跑通静态检查、推上 GitHub。
- 从那套真实 n8n 系统里，已经确认了几个真实事实（不是瞎猜），直接作为这个新 Python 项目的起点配置值写进了 `config/config.yaml`，下面第四节详细列出。

---

## 二、抓取流程（`main.py`）

机制跟 AM1ST 完全一致（详见 AM1ST 自己的 README），只是几个调参数字换成了从 China Breaks 真实 n8n 系统里读到的值：

1. **读信源列表**（Notion 源库）——`in_use` 或 `In_use_2` 勾选为 true 的行都会处理（这两个字段名都已针对 China Breaks 真实库做过live验证，2026-09-05：真实 n8n 系统里这张源表本来就分主流/小众两条线各用一个勾选列，这个 Python 版本不拆两条流程，所以两个勾选列都读、取并集，不然只读一个会漏掉另一半真实信源；除这两个字段外，其余字段名——RSS地址、名称、cookie、网站——仍是从 AM1ST 自己的字段名照抄过来的占位猜测，正式跑之前必须重新核实一遍）。
2. **抓 RSS**——发布时间在最近 **3 小时**以内（`max_publish_age_hours`，跟 AM1ST 一致，2026-09-06 从原来的 6 小时改回 3 小时，对齐当前真实 n8n 系统的 `filter_feed_hours`）。单个信源最多读 **150** 条（`rss.max_items_per_feed`，来自老 n8n 系统的真实调参值）。**没有**总量上限——之前有过一个"整批去重+发布时间过滤后最多留 N 条（按发布时间截断）"的 `rss.max_total_items`，2026-09-06 确认它是导致编辑反馈"中国新闻不够"的真实原因之一（纯按新旧截断、不看相关性，源池里大量通用媒体的稿子会把稀少的真中国新闻挤出候选池），已删除，回到跟 AM1ST 一样不设上限，交给下面的去重/打分环节自己筛选。
3. **去重第一层 —— Redis 精确查重**：Redis key 前缀本项目用的是 `newsroom:chinabreaks:url_hash:`，**不是**真实生产前缀（生产环境实际用 `newsroom:cnbreaks:url_hash:`，2026-09-05 确认）——故意不同，是为了测试阶段不跟还在跑的 n8n 系统共享查重状态。
4. **纯英文过滤 / 同批次语义聚簇 / 跨轮次查重 / 事件热度预览** —— 机制跟 AM1ST 一模一样，见 `core/event_identity.py` / `core/qdrant_store.py` 的代码注释。
5. **AI 打分**（gpt-4o-mini，跟 AM1ST 及本项目"gpt-4o-mini 处处统一"的固定规则一致）：低于 **4 分**（`openai.score_threshold`，来自老 n8n 系统的真实门槛"score > 3"，比 AM1ST 自己的 ≥5 松很多）直接淘汰。
6. **按簇决定要不要真的写进事件库，然后写入候选池**：写进 Notion 候选池时，除了 AM1ST 原有的字段，**新加了一个 `channel_name` 字段**（select，值固定是 `"ChinaBreaks"`）——这是因为 China Breaks 真实候选池这张 Notion 表看起来是被**多个中国相关频道共用**的（同一个库 id 在真实系统里被叫过 "Chinadaily_news_Channel" 和 "china_breaks_Channel" 两个不同的名字），如果不打上自己的频道标签、发布流程查询时不过滤这个字段，很可能会把别的频道写的候选也读进来。

---

## 三、发布流程（`main_publish.py`）

机制同样跟 AM1ST 完全一致，几处不同：

1. **不再有"过期的'前总统特朗普'措辞"过滤**——这是 AM1ST 自己内容流特有的一个bug补丁，跟 China Breaks 毫无关系，已经整个去掉，`agents/candidate_selector.py` 里也确认没有任何遗留引用。
2. **Notion 查询增加 `channel_name` 过滤**——原因同上。
3. **打分门槛**：`candidate_min_score`/`weekday_min_score`/`weekend_min_score` 分别是 4.0/5.0/4.0，比 AM1ST 的 5.0/6.0/5.0 各低 1 分——这不是从老系统里读到的真实值，是我自己做的一个内部一致性修正：既然抓取阶段的打分门槛从 AM1ST 的 5.0 降到了 4.0，发布阶段如果还卡在 5.0，会出现"4.0-4.9 分的候选进得了候选池，但永远没资格被发布流程考虑"这种死区，所以整体往下平移了 1 分。这个数字**这周末真实测试之后建议重新看一眼**。
4. **Trending 信号源换成了 Google News 的"China CCP"搜索结果**（见下面第五节），不再是 AM1ST 用的 NATION 板块。
5. **写手用的是 gpt-4o-mini**，不是老 n8n 系统当时用的 gpt-4.1-mini——这是刻意的：本项目"处处 gpt-4o-mini"是标准规则，不跟着老系统的选型走。

---

## 四、从真实 n8n 系统里确认的事实（写进了本项目的起点配置）

| 项目 | 值 | 备注 |
|---|---|---|
| Notion 源库（RSS） | id `22e16dc99f32808fb86ec094a71fe7af`，名字 "rss_n8n_chinabreaks_to_notion" | 活跃行勾选列**确认**有两个：`in_use`（真实系统的"主流媒体"线用）和 `In_use_2`（"小众媒体"线用）——本项目不拆两条流程，两个都查、取并集；RSS地址/名称/cookie/网站字段名**未验证**，是从 AM1ST 抄的占位猜测 |
| Notion 候选/日志库 | id `22e16dc99f32808788e8dec5cd9107ca`，见过叫 "Chinadaily_news_Channel" 也见过叫 "china_breaks_Channel"（同一个 id） | **已确认**的真实字段：`author`/`description`/`published_at`/`Title`/`url`/`url_to_image`/`post_content`/`llm_score`/`llm_comment`/`content`/`url_hash`/`send_status`/`channel_name`（select，值 "ChinaBreaks"）/`priority`（number，老系统发布查询排序用）。本项目的 `heat_score`/`event_first_seen_at`/`is_hot` 三个字段**在真实库里没有对应列**——真正跑之前要么给库加这三列，要么把这三个字段名重新映射到已有列上。`url_to_image` 和 `priority` 这两个真实存在的列，本项目目前的架构（跟 AM1ST 一样，优先级排序只在内存里算一次、不写回 Notion）暂时用不上。 |
| Redis URL哈希去重 key 前缀 | 本项目用 `newsroom:chinabreaks:url_hash:` | **不是**真实生产前缀——2026-09-05 从真实 n8n 系统里确认，生产环境实际用的是 `newsroom:cnbreaks:url_hash:`。本项目故意不改成一样的，是为了测试阶段不和还在跑的 n8n 系统共享查重状态；等确定要不要跟生产系统状态打通（测试账号 vs 正式账号那个决定）之后再考虑要不要对齐 |
| 真实 Gettr 账号 | 用户名 `chinabreaks`，userId `gettrfoodofficial` | **仅作参考记录**，`.env`/`.env.example` 里 Gettr 相关字段全部留空，没有写进任何真实 token（token 是密钥，用户名/userId不是，但同样没必要写进代码库） |
| 抓取新鲜度窗口 | ~6 小时 | 来自老 n8n `global_config` 节点 |
| RSS 抓取上限 | 单信源 ~150 条 / 整批 ~200 条 | 同上 |
| 抓取打分门槛 | 有效上等于最低 4 分（老系统写的是 "score > 3"） | 同上 |
| 发布调度节奏 | 每 30 分钟一次，错开在 :02/:32 | 跟 AM1ST 的 :17/:47 错开，避免两边同时打同一批共享API；**这只是部署时的建议**，本次构建的 `main_publish.py` 代码本身并没有对齐到具体分钟的逻辑（自循环 + 抖动，跟 AM1ST 一样），真正部署时才需要考虑 |
| Qdrant collection 命名 | `chinabreaks_embeddings` / `chinabreaks_posting_news_embedding` / `chinabreaks_events` | 全小写，跟 AM1ST 的 `am1st_*` 及本生态其它地方已有的 "chinabreaks" 小写惯例保持一致 |

---

## 五、Google News 趋势信号源选择（`agents/trending.py`）

AM1ST 当年是拿 Google News 的 NATION 板块 vs POLITICS 板块做过真实对比测试，选了更贴题的 NATION。这次给 China Breaks 做了同样的对比测试（2026-09-04，实时抓取两个 RSS，人工看内容）：

- **WORLD 板块**（`.../section/topic/WORLD`）：抓到的约30条头条里，绝大多数跟中国/中共完全无关——尼泊尔洪水、德国地方选举、菲律宾副总统政治风波、俄乌局势、伊朗-科威特、丹麦移民政策、苏丹内战、以色列-黎巴嫩——只有 1 条（一个太平洋岛国峰会提到中国导弹试射）真正沾边。
- **"China CCP" 搜索结果**（`.../rss/search?q=China+CCP`）：几乎每一条都是真正对题的内容——中共在美国机构的渗透、习近平人物侧写、政治局会议分析、西藏信息管控、强迫劳动指控、给律师加的"忠诚测试"新法律等等。

结论很明确：**选了 "China CCP" 搜索结果 feed**，不是 WORLD 板块——跟 AM1ST 的 NATION 板块不一样，一个宽泛的板块（不管选哪个）都没法像直接搜索关键词那样把中国/中共相关内容聚拢起来。已在 `agents/trending.py` 代码注释里写清楚这次测试的过程和结论。

---

## 六、自己重新写的内容（不是照抄 AM1ST，是换了领域重新做的）

1. **`core/gazetteer_names.json`**——AM1ST 那份是纯美国国内政治的人名词典（537名国会议员 + 24名内阁成员 + 12名知名人物），对 China Breaks 毫无用处，整个重写成中共/中国相关的类别：
   - `ccp_leadership`（25人）：现任中央政治局常委（7人）+ 政治局全体委员（24人）+ 国务院总理/副总理 + 中央军委领导 + 国安部/中纪委/中宣部/外交部负责人——多数职务已经被政治局名单本身覆盖，只额外补了国安部长陈一新
   - `state_media`（6家）：新华社、央视、CGTN、环球时报、人民日报、中国日报——这几个是**机构名**，代码里专门标成 ORG 而不是 PERSON（跟人名走不一样的实体识别分支，避免把机构名被人名截断逻辑错误地砍成一个词）
   - `notable`（8人）：打分/文案提示词里明确点名的几个人——习近平、王岐山、韩正，"三巨头"里的普京和特朗普，以及提示词里举例提到的欧尔班、卢拉、台湾赖清德
   - `aliases`（1个）：`"William Lai"`（赖清德的英文名，不是从全名拆分出来的）
   - **已知但没修的限制**：`entity_tokens()` 这个函数对多词的人名实体，规则是"只保留最后一个词"（假设西方姓名"名在前、姓在后"的顺序）——但中文姓名是"姓在前、名在后"（比如"Xi Jinping"会被砍成"jinping"而不是真正常用的简称"xi"）。这是原样继承 AM1ST 的机制，按照要求"port unchanged"没有动它，但这个问题对一个中国主题的机器人影响更直接，建议这周末测试时留意一下。

2. **`prompts/same_event_prompt.txt` / `prompts/priority_rank_prompt.txt`**——保留 AM1ST 原有的判断逻辑和格式要求，只把里面举例用的美国政治场景（"the DHS Secretary"、"US-politics headlines"、"MAGA-specific stories" 等）换成中国/中共相关的对应场景。`related_event_prompt.txt` 和 `update_subtype_prompt.txt` 本身就是通用逻辑、没有任何美国政治专属例子，原样照抄，没有改动。

3. **`prompts/scoring_prompt.txt` / `prompts/content_gen_prompt.txt`**——这两个是你已经确认定稿的文案，**逐字节原样抄进来的**，没有做任何改写（来源：`/private/tmp/.../china_breaks_build/scoring_prompt.txt` 和 `content_gen_prompt.txt`）。文案生成的开头风格已经按你的决定走"先说行动者本人，不先说媒体名"（actor-first，不是 outlet-first）。

---

## 七、明确没有搬过来的东西

- **`filter_former_trump`**（`agents/candidate_selector.py`）——这是 AM1ST 自己内容流的一个专属bug补丁（处理过期的"前总统特朗普"措辞），跟 China Breaks 完全无关，整个删掉，`main_publish.py` 里对应的两处调用也一并去掉了。
- **`n8n_am1st_pipelines/` 文件夹的内容**——没有抄进这个新项目。如果需要参考 China Breaks 自己真实的 n8n 导出，本地放了一份在 `n8n_chinabreaks_pipelines/`（已通过 `.gitignore` 排除，不会被提交、不会上 GitHub——这三个文件是直接用 `cp` 复制过来的，过程中没有打开读取过内容，所以那份文件里可能包含的真实 Gettr JWT 没有被看到、更没有被写进本项目任何文件）。

---

## 八、涉及的外部服务

| 服务 | 用途 | 备注 |
|---|---|---|
| Notion（源库） | 存 RSS 信源列表 | 用 `NOTION_API_KEY` |
| Notion（候选池库） | 抓取流程写、发布流程读 | 用 `NOTION_CANDIDATE_API_KEY`，可能是多频道共享库，见上文 channel_name 说明 |
| Notion（热点标记库） | 人工标记突发新闻，多个兄弟机器人共用 | 用 `NOTION_HOT_TOPICS_API_KEY`，本项目自己的 db id 尚未配置 |
| Redis | 抓取阶段第一层去重（URL 哈希）+ 实体命中历史索引 | key 前缀见上文 |
| Qdrant | 三个独立 collection：`chinabreaks_embeddings` / `chinabreaks_posting_news_embedding` / `chinabreaks_events` | 复用团队现有共享集群的设想，跟 AM1ST 一样 |
| OpenAI（gpt-4o-mini） | 打分 + 生成文案 + 优先级重排 + 事件身份验证 + embedding | 支持双 key 容错（`core/openai_client.py`），主 key 限流/欠费时自动切换 |
| Gettr | 实际发帖 | 本次构建**完全没有配置真实凭证**，也没有真实发布测试 |
| 共享无头 Chromium 渲染服务 | 部分信源反爬/付费墙的兜底抓取 | `core/render_client.py`，硬编码指向 `http://127.0.0.1:8811` —— 2026-09-05 起本项目代码已部署在 `gettr-news-agents-02` 上，这台机器本来就常驻跑着这个共享服务（AM1ST 等其它 bot 也用同一个），确认可达；本地 Mac 上跑当然连不上，会优雅地"查不到就跳过"，不会报错崩溃 |

---

## 九、还需要你确认/决定的事

1. Notion 源库（`rss_n8n_chinabreaks_to_notion`）除了 `in_use`/`In_use_2`（2026-09-05 已双双确认）之外的字段名（RSS地址、名称、cookie、网站）——目前仍是从 AM1ST 抄的占位猜测，正式跑之前需要对真实库做一次 live schema 读取核实。
2. Notion 候选池库缺少 `heat_score`/`event_first_seen_at`/`is_hot` 三列——需要决定是给库加这三列，还是把这三个字段重新映射到库里已有的其它列。
3. `热点标记`（hot_topics）库的 db id 目前是空的——如果打算让 China Breaks 复用 AM1ST 现在已经在用的那张共享表，需要把真实 db id 填进 `.env`。
4. `heat.major_outlets`（用于给信源可信度加权的"主流媒体名单"）我换成了 Reuters/AFP/DW/Nikkei/Kyodo/VOA/RFA/CNA——这不是从真实系统里读到的值，是我按内容提示词里点名的可信信源自己做的替换，值得你确认一下是否合适。
5. `publish.candidate_min_score`/`weekday_min_score`/`weekend_min_score` 的 4.0/5.0/4.0——同样不是真实值，是我为了跟抓取阶段 4.0 分的门槛保持内部一致自己往下平移的，这周末拿到真实数据后建议重新看一遍（抓取阶段本身的 4.0 分门槛已在 2026-09-05 对照真实 n8n 系统确认无误，不用再改）。
6. ~~`entity_tokens()` 对中文姓名"姓在前"的顺序处理不准确~~ —— 2026-09-05 已修：凡是词表里有的人名（`ccp_leadership`/`notable`/`us_officials`/`world_leaders`），现在会按词表自带的 short_form 取简称，不再盲目取词组最后一个词；词表之外、靠统计模型识别出来的生僻中文人名仍会退回旧逻辑，这是词表覆盖范围的固有局限，不是新引入的问题。
7. 打分 prompt（2026-09-05 已按频道定位和 AM1ST 现在更成熟的模板整个重写）里提到的"trending headlines"佐证信号，抓取阶段之前一直没真的接进 `main.py`——已在同一天补上（`agents/trending.py` 的 Google News "China CCP" 订阅源，每个抓取周期只拉一次，传给每条候选打分），否则 prompt 说的和代码实际做的会对不上。

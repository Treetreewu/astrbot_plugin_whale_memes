# Whale Memes：萌百查梗与学习

独立的双框架插件：AstrBot 4.29.3 与 MaiBot 1.3.18 / SDK 2.19.0 共用学习核心。公开仓库为 `Treetreewu/astrbot_plugin_whale_memes`。不依赖 whale-qqbot、做梦插件、LivingMemory 或额外服务。

## 功能与边界

模型调用一次 `meme_notebook(action="learn", term="梗名")`，插件负责：

1. 查当前 bot、人格、会话的笔记；有效的精确名称或别名命中直接返回。
2. 未命中或过期时，优先调用宿主原生搜索，最多两种公共关键词查询。
3. 从候选里筛选萌百中文站条目，读取有限数量的页面；读取失败时可按配置使用原生搜索摘要。
4. 将本轮资料交给当前配置的模型整理含义、使用语境和歧义。
5. 检查模型所选来源编号及逐字引文，匹配实际收到的资料后才保存。URL、证据等级、时间及资料哈希由代码确定，模型不能覆盖。

其他操作：`refresh` 强制重新核实，用于纠错；`lookup` 只读已有笔记；`forget` 按完整条目名精确遗忘。v2 不再允许模型直接 `save` 任意笔记。

**`source_quote_matched` 只说明引文存在于本轮来源资料，不代表语义或梗出处绝对正确。** 原生搜索摘要仍标注 `search_excerpt`，实际读取正文才标注 `page`。摘要服务和模型都可能出错；无依据、超时或来源不匹配时不保存。

MaiBot 复用 SDK `web.search`、`web.fetch`、`llm.generate`。AstrBot 目前适配原生 DeepSeek search/fetch 的结构化结果；模型通过当前会话 provider 调用。Codex 或其他未配置结构化搜索的路线，可以让原生模型尝试发现链接，再由插件读取公开页面。**模型自报的摘要不作为证据**；访问受限、不能读到正文时返回 `unverified`。不保证 Codex 每次都会真正执行搜索，也不保证每个词条可访问。不会绕过 403、登录、验证码或反爬措施。

这不是全站爬虫、训练集、定时补课或独立 Agent。聊天模型仍决定何时调用工具，以及怎样使用返回的知识。工具说明已经包含查梗和自然接话规则，无需往人格里粘贴长篇操作流程。

## 安装

### AstrBot

通过插件管理器安装本仓库，或将完整仓库放入原插件目录 `data/plugins/whale_meme_notebook/`。该目录保留与 v1 相同名字，避免重复安装；入口为 `main.py`。

- 在原有人格工具白名单中保留/加入 `meme_notebook`，不改变 persona_id。
- 插件配置界面使用 `_conf_schema.json`；修改后重载插件。
- 默认数据仍在 `data/plugin_data/whale_memes/notes.sqlite3`。
- 模型沿用当前会话 provider；可以用 `provider_id` 显式覆盖。调用不携带聊天历史。

### MaiBot

将完整仓库放入 `plugins/whale_memes/`，入口为 `plugin.py`、`_manifest.json`。SDK 按原生配置模型生成配置，`[learning]` 下管理学习参数。

- 稳定插件 ID 仍为 `whale.meme-notebook`，工具仍为 `meme_notebook`。
- manifest 声明 `web.search`、`web.fetch`、`llm.generate`，遵循宿主能力权限设置。
- 默认保存到 SDK `ctx.data_dir/notes.sqlite3`。从 QQBot v1 升级时显式设 `storage_path=/MaiMBot/data/whale_memes/notes.sqlite3`，以继续读取原数据库。
- `model_task` 默认 `learner`；使用宿主已有任务和模型配置，不复制密钥。

两个框架插件包都自带 `whale_memes/`。部署包可只读；MaiBot 的原生 config.toml 所在目录需可写，容器部署可只读挂载入口文件和核心目录，保留父目录可写。运行数据必须在持久可写目录。

## 配置

完整默认值位于 `whale_memes/default_config.json`，与 `Config`、两套原生配置 schema 由测试核对。可选环境变量 `WHALE_MEMES_CONFIG` 指向操作员拥有的 JSON 文件，覆盖原生插件配置；该文件缺失或配置非法时拒绝加载。重载才应用修改，不自动改写源配置。

| 参数 | 默认值 | 含义 |
| --- | --- | --- |
| enabled | true | 启用功能 |
| ttl_days | 30 | 资料复核周期，读取不会延长 |
| max_notes_per_scope / max_notes_total | 500 / 5000 | 会话和单库容量，最久未使用优先淘汰 |
| max_searches / max_results / max_pages | 2 / 3 / 2 | 每轮查询、每查询候选和每轮页面预算 |
| max_concurrent | 2 | 进程内同时学习上限 |
| learning_per_hour | 6 | 每作用域小时学习次数，失败计入、重启保留 |
| timeout_seconds / operation_timeout_seconds | 45 / 12 | 整轮及每次外部调用超时（秒） |
| max_source_chars | 8000 | 每来源传入整理模型的文本上限 |
| allow_model_discovery | true | 原生搜索无结果时尝试让模型发现链接；返回文字不作为证据 |
| allow_search_excerpt | true | 允许带等级标记的原生搜索摘要笔记 |
| storage_path | 空 | 自定义数据库路径，否则原生默认 |
| model_task / provider_id | learner / 空 | MaiBot 模型任务、AstrBot provider 覆盖 |
| maibot_persona | maibot-native-v1 | MaiBot 数据人格作用域，升级不要随意更改 |

超时会停止插件等待及后续写入，无法保证已经提交到上游的模型请求同时停止计费。最多两次模型链接发现和一次整理请求；宿主内部重试策略仍由宿主控制。请求失败不记录密钥、完整上游报错或群聊内容。

## 数据兼容和生命周期

保留 v1 的 SQLite `notes` 表和作用域计算。首次操作只增加预算和版本表，不迁移或删除原表。旧的 `model_reported_source` 笔记可查询，但必须重新取证才能作为有效缓存。两只 bot、不同人格及会话不共享笔记。

`forget` 会提高当前作用域的版本，即使条目尚未存在，也会阻止已经排队或正在执行的旧学习请求重新保存。并发更新后其他在途结果保守地失效。插件卸载取消在途任务；MaiBot 配置更新关闭旧实例后重建。禁用/卸载不会删除笔记或私人记忆。笔记只记录公共梗知识，不接入做梦成长或个人记忆库。

从 v1 升级时，删除人格中的 `[萌百梗学习]…[/萌百梗学习]` 长流程块，保留原有人格风格与工具白名单。旧指令中的 `save` 会被拒绝并提示使用新动作。QQBot 集成应锁定完整提交及文件哈希，升级版本目录不能原地覆盖。

## 验证

Python 3.11+；核心只使用标准库，框架依赖由各自宿主提供。

```bash
python -m pip install maibot-plugin-sdk==2.19.0 packaging docstring-parser
python -m unittest discover -s tests -v
# 可选：读取固定上游 Git 对象，校验真实 manifest、SDK 生命周期、AstrBot 工具装饰器
python scripts/check-meme-native.py --source-root /path/to/upstream-clones
```

测试使用合成资料和临时数据库，不访问真实模型、QQ 或生产记忆。覆盖完整学习链、来源伪造拒绝、摘要降级、Codex 链接发现边界、限额、超时、旧库兼容、隔离、排队/在途遗忘、卸载和原生配置加载。真实网络与模型质量、完整宿主加载和 QQ 对话仍需目标环境验收。

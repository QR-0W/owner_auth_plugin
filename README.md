# 用户身份验证插件

通过 QQ 号精确认证配置中的特殊用户，并把可信身份及分阶段关系便签发送给 MaiBot 的 Planner 和 Replyer。

v2.2.0 面向 MaiBot 1.0.x / maibot_sdk 2.x，使用官方 Hook，不修改 MaiBot 主程序，不读取人物画像作为身份依据。

## 功能

- 只使用平台提供的 QQ 号认证身份，昵称、群名片和消息正文不能创建身份。
- 使用 `(session_id, message_id)` 精确绑定发送者，不再用群聊最近发言者兜底。
- Planner 为具体消息添加 `verified_identity` 和 `relation` 临时属性。
- 每个特殊用户分别配置 Planner 规划便签和 Replyer 回复便签。
- 任一便签留空时，该阶段只接收可信身份，即 Identity Only。
- 同一 QQ 可以同时配置全局身份和群专属身份，群专属配置优先。
- 未配置或未命中的普通用户不会收到任何身份或安全提醒 Prompt。
- 人物画像仍由 MaiBot 自行注入，只作为背景参考，不参与本插件认证。

## 安装

1. 将插件目录放入 MaiBot 的 `plugins/`。
2. 重启 MaiBot 或重载插件。
3. 在 WebUI 的插件配置页设置用户数量、QQ、关系称呼和两张便签。
4. 使用 `/owner_auth_status` 查看索引和注入状态。

未配置有效 QQ 时，插件保持静默。

## 配置

### 通用设置

| 字段 | 说明 | 默认 |
| --- | --- | --- |
| 启用插件 | 关闭后不再传递身份或关系便签 | 开 |
| 用户数量控制 | 生成 `user1..userN` 固定槽位 | 1 |
| 私聊中注入 | 是否在私聊中认证特殊用户 | 开 |
| 同时注入行动规划 | 是否向 Planner 传递身份与规划便签 | 开 |
| 插件记录注入日志 | 记录身份命中和失败关闭 | 开 |
| 日志显示等级 | `INFO` / `DEBUG` / `WARNING` | INFO |
| 身份索引会话有效期 | 清理不活跃会话索引；活跃会话中的单条消息不按 TTL 中途掉标 | 300 秒 |

### 每个用户

| 字段 | 说明 |
| --- | --- |
| 启用此用户 | 临时停用该槽位，不清空配置 |
| 用户 QQ 号 | 唯一身份凭据 |
| 用户昵称 | 关系称呼，例如“主人”或“老师” |
| 限制群聊 | 留空表示全局，填写群号表示仅在该群生效 |
| Planner 规划便签 | 只影响行动规划；留空表示 Planner Identity Only |
| Replyer 回复便签 | 只影响最终措辞；留空表示 Replyer Identity Only |

两个便签都支持以下占位符：

| 占位符 | 含义 |
| --- | --- |
| `{nickname}` / `{owner_nickname}` | 配置中的关系称呼 |
| `{display_name}` / `{user}` | 已清洗的聊天显示名 |
| `{qq}` / `{owner_qq}` / `{user_qq}` | 已认证 QQ 号 |
| `{msg}` / `{message}` | 已清洗并截断的目标消息文本 |
| `{owner_names}` | 所有启用用户的关系称呼 |

配置值只做单遍占位符替换，不会二次展开。

## 注入流程

```text
入站消息
  -> chat.receive.after_process
  -> 保存 (session_id, message_id) 对应的 QQ 发送者快照

Planner 请求
  -> maisaka.planner.before_request
  -> 按上下文中的 msg_id 精确查快照
  -> 在本次消息副本增加 verified_identity / relation
  -> 在动态尾部添加非空 Planner 规划便签

Replyer 请求
  -> maisaka.replyer.before_request
  -> 按 reply_message_id 精确查目标发送者
  -> 在最终 user 动态尾部添加身份凭证和非空 Replyer 回复便签
```

任何精确定位失败都会跳过注入。插件不会把同一会话的最近发言者当作当前回复对象。

## Prompt Cache

- Planner 的身份字段协议固定追加在 system 尾部，每轮内容一致。
- 同一历史消息的身份属性由不可变消息号决定，连续请求保持一致。
- 每用户规划便签放在 MaiBot 已有当前时间等动态内容之后。
- Replyer 身份和回复便签使用最终 `extra_prompt`，不动态修改 system。
- Planner 不会在请求期间补查旧历史消息，避免相同历史前缀从无标签变成有标签。

## 人物画像

MaiBot 会按 `platform + user_id` 选择相关人物画像并作为 Planner 内部参考。画像查询可能超时、为空或只包含部分背景，而且不会直接注入 Replyer。因此本插件不把人物画像当作认证结果：

```text
身份插件：确定“这个账号是谁”以及对该身份使用哪张关系便签
人物画像：补充兴趣、经历、近期互动等背景
```

画像中的昵称或关系描述不能创建、覆盖 `verified_identity`。

## v2.1 配置迁移

升级到 v2.2.0 时：

- 每个 `[userN].prompt_template` 原样迁移到 `[userN].replyer_prompt_template`。
- Planner 使用新的独立短默认便签。
- WebUI 和日志会提示检查并按需手工拆分旧关系 Prompt。
- `[non_owner]` 及旧版非用户注入配置会被移除。
- 迁移写回后具有幂等性，不会重复修改。

建议升级后检查两张便签。若只想验证身份传递，将对应便签留空即可。

## 排障

`/owner_auth_status` 会显示：

- 已配置用户槽位；
- 当前用户是否命中；
- 消息身份索引条数和会话数；
- Planner 累计身份标注；
- Replyer 累计精确认证；
- 身份定位失败关闭次数；
- 最近一次注入阶段和脱敏 QQ。

将日志等级设为 `DEBUG` 可查看实际注入文本。日志中的 QQ 默认脱敏。

## 兼容性

- MaiBot：`1.0.0` 到 `1.99.x`
- maibot_sdk：`2.0.0` 到 `2.99.x`
- Python：跟随 MaiBot 运行环境
- 第三方依赖：无

## 许可

GPL-3.0-or-later。原作者：[风花叶](https://github.com/khiqwq)。

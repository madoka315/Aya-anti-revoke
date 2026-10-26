# Aya防撤回

HoshinoBot 的群聊防撤回插件。文字、图片都支持。

实现思路参考 [astrbot_plugin_anti_revoke](https://github.com/Foolllll-J/astrbot_plugin_anti_revoke)，针对 HoshinoBot（nonebot1 + OneBot v11 协议端）重写。被撤回内容回显前会做 CQ 码转义

## 工作原理

- 收到每条群消息时，把文本内容存进内存缓存，图片同时下载到磁盘缓存
- 消息被撤回时，直接用缓存回显，缓存未命中才走 `get_msg` 兜底

## 启用

本插件默认关闭且不出现在帮助列表。在需要的群里由群管理发送：

```
启用 防撤回
```

## 配置说明

`config.json`（与插件同目录），改完重启生效：

| 字段 | 默认值 | 说明 |
|---|---|---|
| `image_recall` | `true` | 图片防撤回，关掉后图片只显示 `[image]` 占位符 |
| `cache_dir` | `cache` | 图片缓存目录（相对） |
| `cache_ttl_hours` | `6` | 缓存保留时长（内存与磁盘一致），过期自动清理 |
| `max_image_mb` | `10` | 单图大小上限 |
| `max_images_per_msg` | `9` | 单条消息最多缓存的图片数 |


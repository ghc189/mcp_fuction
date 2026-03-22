# Bailian Voice Clone MCP

一个可部署到阿里云 Function AI 的 `stdio` MCP，用于：

- 创建声音克隆
- 轮询音色状态
- 查询单个音色
- 列出音色
- 删除音色
- 用复刻音色做语音合成

## 我替你选的模型

推荐模型：`cosyvoice-v3.5-plus`

原因：

- 阿里云官方“模型选型”里，对“品牌形象、专属声音、扩展系统音色等语音定制（基于音频样本）”直接推荐 `cosyvoice-v3.5-plus`
- 你现在走的是百炼 `cn-beijing` 路线，而 `cosyvoice-v3.5-plus` 正好仅在北京地域可用
- `cosyvoice-v3.5-flash` 更偏低成本、低延迟客服/语音助手场景，不是官方首推的高还原声音克隆模型

## 工具列表

- `create_voice_clone`
- `query_voice`
- `wait_for_voice_ready`
- `list_voices`
- `delete_voice`
- `synthesize_with_cloned_voice`

## 前提条件

1. 你已经在阿里云百炼北京地域开通模型服务
2. 你已经拿到北京地域 API Key
3. 你有一个公网可访问的音频 URL
4. 录音建议使用官方推荐规格：16-bit / 16kHz / 时长 10 到 200 秒

## 本地启动

1. 安装依赖

```powershell
cd C:\Users\29932\bailian-voice-clone-mcp
python -m pip install -r requirements.txt
```

2. 配置环境变量

```powershell
$env:DASHSCOPE_API_KEY="sk-xxxx"
$env:DASHSCOPE_REGION="cn-beijing"
$env:BAILIAN_TTS_MODEL="cosyvoice-v3.5-plus"
```

3. 启动 MCP

```powershell
python server.py
```

4. 做一次本地冒烟测试

```powershell
python smoke_test.py
```

## Path B：部署到阿里云 Function AI

### 1. 准备代码仓库

把这个目录推到 GitHub 或阿里云 Codeup：

- `server.py`
- `requirements.txt`
- `.env.example`
- `README.md`

### 2. 在 Function AI 创建 MCP 服务

1. 登录 Function AI 控制台
2. 创建空白项目
3. 新建服务，选择 `MCP 服务`
4. 传输类型选择 `SSE`
5. 开启鉴权
6. 运行环境选择 `Python`
7. 绑定你的代码仓库

### 3. 配置构建和启动

建议值：

- 构建命令：`pip install -t . -r requirements.txt`
- 启动命令：`python server.py`

资源建议：

- vCPU：1
- 内存：2 GB
- 弹性策略：`极速模式`
- 预置快照：`1`
- 实例上限：`1`

### 4. 配置环境变量

在 Function AI 的变量管理里新增：

- `DASHSCOPE_API_KEY`
- `DASHSCOPE_REGION=cn-beijing`
- `BAILIAN_TTS_MODEL=cosyvoice-v3.5-plus`
- `INLINE_AUDIO_BASE64_LIMIT=300000`

### 5. 部署并测试

部署成功后，Function AI 会给你一个公网 SSE 地址，通常是：

```text
https://xxxx.cn-beijing.fcapp.run/sse
```

先在 Function AI 控制台直接测试工具是否可用。

## 注册到百炼 MCP 管理

1. 打开百炼控制台 -> MCP 管理 -> 自定义服务
2. 点击 `+创建 MCP 服务`
3. 选择 `使用脚本部署`
4. 安装方式选 `http`
5. 填入你的 SSE 地址

配置示例：

```json
{
  "mcpServers": {
    "voice-clone-mcp": {
      "url": "https://xxxx.cn-beijing.fcapp.run/sse"
    }
  }
}
```

## 使用顺序建议

1. 调 `create_voice_clone`
2. 调 `wait_for_voice_ready`
3. 状态变成 `OK` 后，调 `synthesize_with_cloned_voice`

## 示例参数

### 创建声音克隆

```json
{
  "audio_url": "https://your-public-audio-url/sample.wav",
  "prefix": "myvoice01",
  "language_hint": "zh",
  "target_model": "cosyvoice-v3.5-plus",
  "region": "cn-beijing"
}
```

### 合成语音

```json
{
  "text": "你好，这是一段使用复刻音色生成的演示语音。",
  "voice_id": "cosyvoice-v3.5-plus-myvoice01-xxxxxxxx",
  "target_model": "cosyvoice-v3.5-plus",
  "region": "cn-beijing",
  "inline_base64": true
}
```

## 注意事项

- 声音克隆和声音合成用的 `target_model` 必须一致，否则合成会失败
- `audio_url` 必须公网可访问
- `prefix` 建议只用小写字母、数字、下划线，长度不超过 10
- `synthesize_with_cloned_voice` 默认会把音频落到临时目录；在云端想长期保存，下一步建议接 OSS

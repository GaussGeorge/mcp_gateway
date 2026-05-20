# 真实 LLM 实验 — 工具使用示例文档

> **场景**: 用户通过 LLM Agent（如 ChatGPT、Claude、DeepSeek）完成多步骤任务，Agent 自动选择工具并链式调用。
> 以下示例展示了每个工具解决的典型用户问题。

---

## 1. `calculate` — 精确数学计算

**解决的痛点**: LLM 在数学运算上经常"幻觉"，给出近似值或错误答案。此工具提供保底精确计算。

### 示例 A: 财务预算计算
```
用户: "我有 3 笔投资，分别是 12500.75 元、8300.50 元和 15200.25 元，年化收益率 4.5%，一年后总收益是多少？"

Agent 调用:
  Step 1: calculate(operation="add", a=12500.75, b=8300.50)       → 20801.25
  Step 2: calculate(operation="add", a=20801.25, b=15200.25)      → 36001.50
  Step 3: calculate(operation="multiply", a=36001.50, b=0.045)    → 1620.0675
  
Agent 回复: "三笔投资总计 36,001.50 元，按 4.5% 年化收益率，一年后总收益为 1,620.07 元。"
```

### 示例 B: 单位换算
```
用户: "5.2TB 等于多少 MB？"

Agent 调用:
  Step 1: calculate(operation="multiply", a=5.2, b=1048576)  → 5,452,595.2
  
Agent 回复: "5.2TB = 5,452,595.2 MB"
```

### 示例 C: 数学验证
```
用户: "12 的阶乘是多少？"

Agent 调用:
  Step 1: calculate(operation="factorial", a=12)  → 479001600
  
Agent 回复: "12! = 479,001,600"
```

---

## 2. `real_weather` — 实时天气查询

**解决的痛点**: LLM 训练数据有截止日期，无法回答"现在"的天气。此工具调用 wttr.in 获取实时数据。

### 示例 A: 出行决策
```
用户: "我明天想去北京爬长城，今天北京天气怎么样？需要带雨伞吗？"

Agent 调用:
  Step 1: real_weather(city="Beijing", format="detailed")
  → {"city":"Beijing","temp_C":"22","humidity":"45","weather_desc":"Partly cloudy","wind_speed_kmph":"15"}

Agent 回复: "北京今天部分多云，温度 22°C，湿度 45%，风速 15km/h。天气不错，不需要带雨伞，但建议带件薄外套防风。"
```

### 示例 B: 多城市对比
```
用户: "东京和伦敦哪个更适合这周末户外活动？"

Agent 调用:
  Step 1: real_weather(city="Tokyo")    → "Tokyo: Sunny +25°C 30% 8 km/h"
  Step 2: real_weather(city="London")   → "London: Light rain +12°C 85% 22 km/h"

Agent 回复: "东京 25°C 晴天更适合户外活动。伦敦目前小雨 12°C，不太适合。"
```

### 示例 C: 穿衣建议
```
用户: "首尔现在多少度？穿什么合适？"

Agent 调用:
  Step 1: real_weather(city="Seoul", format="detailed")
  → {"temp_C":"8","feels_like_C":"4","weather_desc":"Clear","humidity":"55"}

Agent 回复: "首尔现在 8°C，体感温度 4°C，晴天。建议穿厚外套+毛衣，早晚温差大注意保暖。"
```

---

## 3. `text_format` — 文本格式化与处理

**解决的痛点**: LLM 处理结构化文本（JSON、正则、编码）时容易出错。此工具提供精确的文本操作。

### 示例 A: API 响应格式化
```
用户: "帮我把这段 JSON 格式化一下：{"name":"张三","age":28,"skills":["Python","Go","React"]}"

Agent 调用:
  Step 1: text_format(operation="json_format", text='{"name":"张三","age":28,"skills":["Python","Go","React"]}')
  → 格式化的 JSON（缩进 2 空格）

Agent 回复: (输出美化后的 JSON)
```

### 示例 B: 论文字数统计
```
用户: "帮我数一下这段摘要有多少字：'本文提出了一种基于动态定价的MCP工具调用治理框架...'"

Agent 调用:
  Step 1: text_format(operation="word_count", text="本文提出了一种基于动态定价的MCP工具调用治理框架...")
  → {"chinese_characters": 156, "english_words": 3, "total_characters": 180, "lines": 1}

Agent 回复: "这段摘要共 156 个中文字符，总计 180 个字符。"
```

### 示例 C: 日志提取
```
用户: "从这段服务器日志中提取所有 IP 地址"

Agent 调用:
  Step 1: text_format(operation="regex_extract", text="...(日志内容)...", pattern="\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}")
  → {"matches": ["192.168.1.1", "10.0.0.5", "172.16.0.100"], "count": 3}

Agent 回复: "日志中发现 3 个 IP 地址: 192.168.1.1, 10.0.0.5, 172.16.0.100"
```

### 示例 D: Base64 解码
```
用户: "帮我解码这段 Base64：SGVsbG8gV29ybGQ="

Agent 调用:
  Step 1: text_format(operation="base64_decode", text="SGVsbG8gV29ybGQ=")
  → "Hello World"

Agent 回复: "解码结果: Hello World"
```

---

## 4. `real_web_search` — 实时网络搜索

**解决的痛点**: LLM 知识有截止日期，无法检索最新事件、论文、价格等实时信息。

### 示例 A: 技术调研
```
用户: "2024 年最流行的 LLM Agent 框架有哪些？"

Agent 调用:
  Step 1: real_web_search(query="LLM agent framework 2024 popular", max_results=5)
  → [
      {"title":"LangGraph: Multi-Agent Orchestration","url":"...","snippet":"..."},
      {"title":"AutoGen: Microsoft's Agent Framework","url":"...","snippet":"..."},
      {"title":"CrewAI: Role-based Agent Collaboration","url":"...","snippet":"..."},
    ]

Agent 回复: "2024 年最流行的 LLM Agent 框架包括: LangGraph (多Agent编排)、AutoGen (微软)、CrewAI (角色协作)..."
```

### 示例 B: 事实核查
```
用户: "GPT-4o 的上下文窗口是多少 token？"

Agent 调用:
  Step 1: real_web_search(query="GPT-4o context window token limit 2024", max_results=3)
  → [{"title":"GPT-4o Model Card","snippet":"128K context window..."}]

Agent 回复: "GPT-4o 支持 128K token 的上下文窗口。"
```

### 示例 C: 最新新闻
```
用户: "最近有什么关于 MCP 协议的新动态？"

Agent 调用:
  Step 1: real_web_search(query="MCP protocol model context protocol news 2024")
  → [{"title":"Anthropic launches MCP","snippet":"..."},
     {"title":"MCP adoption grows","snippet":"..."}]

Agent 回复: "根据最新搜索结果，Anthropic 发布的 MCP 协议正在被越来越多的 AI 工具采用..."
```

---

## 5. `deepseek_llm` — LLM 推理引擎

**解决的痛点**: 需要深度推理、翻译、代码生成等 GPU 密集型任务时，作为"工具中的工具"完成复杂子任务。

### 示例 A: 长文摘要
```
用户: "帮我总结这篇 5000 字的论文关键内容"

Agent 调用:
  Step 1: deepseek_llm(operation="summarize", text="(论文全文)", max_tokens=300)
  → {"result":"本文提出了MCP-DP框架，核心贡献包括三个创新点: (1) 预飞原子准入...","usage":{"total_tokens":450}}

Agent 回复: "论文核心内容: 提出了 MCP-DP 框架，包含三个创新点..."
```

### 示例 B: 多语言翻译
```
用户: "把这段技术文档翻译成中文"

Agent 调用:
  Step 1: deepseek_llm(operation="translate", text="Dynamic pricing adapts the cost of tool invocations based on real-time system load...", target_language="Chinese")
  → {"result":"动态定价根据实时系统负载调整工具调用的成本...","usage":{"total_tokens":280}}

Agent 回复: "翻译结果: 动态定价根据实时系统负载调整工具调用的成本..."
```

### 示例 C: 逻辑推理
```
用户: "分析一下为什么微服务架构下工具调用需要准入控制"

Agent 调用:
  Step 1: deepseek_llm(operation="reason", text="Why do multi-step LLM agent tool calls in microservice architectures need admission control?", max_tokens=500)
  → {"result":"在多步骤 LLM Agent 工具调用中，准入控制是必需的，原因如下: 1. 级联失败风险...","usage":{"total_tokens":620}}

Agent 回复: "准入控制的必要性分析: 1. 级联失败风险 — 当前步骤成功但后续步骤超时，前面的计算全部浪费..."
```

### 示例 D: 代码生成
```
用户: "用 Python 写一个指数退避重试装饰器"

Agent 调用:
  Step 1: deepseek_llm(operation="code", text="Write a Python exponential backoff retry decorator with configurable max retries and base delay", max_tokens=400)
  → {"result":"```python\nimport time, functools\n\ndef retry_with_backoff(max_retries=3, base_delay=1.0):\n...```","usage":{"total_tokens":350}}

Agent 回复: "(返回完整的装饰器代码)"
```

---

## 多步骤组合示例 — Agent 工作流

### 场景: "帮我查一下北京今天的天气，计算出适合的出行时间，并翻译成英文给外国朋友"

```
Agent 执行流:
  Step 1: real_weather(city="Beijing", format="detailed")
          → 获取温度 22°C、湿度 45%、晴天
  
  Step 2: calculate(operation="subtract", a=22, b=5)
          → 17°C (早晚预估温差)
  
  Step 3: text_format(operation="word_count", text="建议上午 9-11 点或下午 3-5 点出行...")
          → 统计中文字符数
  
  Step 4: deepseek_llm(operation="translate", text="北京今天晴天22°C，建议上午9-11点出行...", target_language="English")
          → "Beijing is sunny today at 22°C. Recommended outdoor time: 9-11 AM..."

Agent 回复: 
  "今天北京晴天 22°C，建议出行时间 9-11 点。
   English version for your friend:
   Beijing is sunny today at 22°C. Recommended outdoor time: 9-11 AM..."
```

### 场景: "帮我调研微服务限流算法，总结核心区别"

```
Agent 执行流:
  Step 1: real_web_search(query="rate limiting algorithms comparison token bucket leaky bucket", max_results=5)
          → 获取 5 篇相关文章摘要
  
  Step 2: deepseek_llm(operation="summarize", text="(搜索结果合并)", max_tokens=400)
          → "核心限流算法对比: 1. Token Bucket... 2. Leaky Bucket... 3. Sliding Window..."
  
  Step 3: text_format(operation="json_format", text='{"algorithms":[...]}')
          → 格式化为结构化输出

Agent 回复: "微服务限流算法主要有三种..."
```

---

## 工具注册信息总览

| 注册名 | 模块 | 类别 | 典型延迟 | 后端 | 核心用途 |
|--------|------|------|---------|------|---------|
| `calculate` | calculator.py | 轻量级 | <1ms | 本地 CPU | 精确数学运算，避免幻觉 |
| `real_weather` | real_weather.py | 轻量级 | 200-800ms | wttr.in | 实时天气查询 |
| `text_format` | text_formatter.py | 轻量级 | <1ms | 本地正则 | 文本格式化/解析/编码 |
| `real_web_search` | real_web_search.py | 中量级 | 500-3000ms | Tavily/SerpAPI | 实时网络信息检索 |
| `deepseek_llm` | deepseek_llm.py | **重量级** | 1-10s | GLM-4-Flash | 摘要/翻译/推理/代码生成 |

> **实验核心观察**: `deepseek_llm` 是主要的过载触发器 — GPU 密集、延迟高且不稳定、
> 共享 GPU 资源导致排队效应。PlanGate 的准入控制在高并发下有效减少级联失败和 token 浪费。

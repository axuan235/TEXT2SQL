# TEXT2SQL —— 用户描述转 SQL 模型系统

基于需求说明书（`需求说明书.txt`）实现的 Python/FastAPI 项目，覆盖五大功能：

1. **特征提取** —— 从 SQL 模型中提取每个字段的计算逻辑（特征 DSL），计算优化 + 命名规范化后，
   拆分为限定词库与特征结构库入库（MD5 去重）。
2. **特征生成** —— 从自然语言特征描述出发，结构化切分 → 知识库检索 → 限定词参数实例化 →
   候选集生成与筛选 → 保留取数逻辑。
3. **计算规划** —— 检测特征间共同计算基础并合并为同一 SQL；分析字段依赖；计算结构树转 SQL 模型。
4. **用户描述转 SQL 模型** —— 产品 story 全流程（系统工作1/2/3）。
5. **规则翻译** —— 基于配置文件（比较翻译规则/函数翻译规则）与表结构信息，
   将条件、函数、DSL 翻译为自然语言（`transCol` / `transValue` / `stringFormat`）。

---

## 目录结构

```
text2sql/
├── app/
│   ├── main.py                # FastAPI 入口（uvicorn app.main:app）
│   ├── api/                   # 三个业务 API 路由与 Pydantic 模型
│   ├── core/                  # 配置加载（settings.yaml）与异常
│   ├── dsl/                   # 特征 DSL：AST / 解析器 / 优化器 / SQL 生成
│   ├── feature/               # 特征描述结构化 / 提取 / 生成 / 计算规划
│   ├── translate/             # 规则翻译引擎（transCol/transValue/stringFormat）
│   ├── kb/                    # 知识库（JSONL + MD5 去重）与检索器
│   ├── llm/                   # LLM/Embedding 客户端（未配置时离线兜底）
│   └── services/              # 系统工作1/2/3 业务服务
├── config/                    # 全部配置文件（见下）
├── data/                      # 知识库（纯文本 JSONL）与映射字典
├── scripts/                   # init_kb.py 初始化 / demo.py 端到端演示
└── tests/                     # pytest 测试（32 用例）
```

## 快速开始

```bash
cd text2sql
pip install -r requirements.txt

# 1) 启动 API（默认 http://127.0.0.1:8000，可在 config/settings.yaml 修改端口）
uvicorn app.main:app --reload

# 2) 端到端演示（离线，无需 LLM）
python scripts/demo.py

# 3) 从需求目录的 _tmp.txt / 样例 xls 重新初始化配置
python scripts/init_kb.py

# 4) 运行测试
python -m pytest tests -v
```

## 三个业务 API（对应系统工作1/2/3）

| 接口 | 对应系统工作 | 说明 |
|---|---|---|
| `POST /api/v1/parse` | 系统工作1 | 用户描述拆解确认，结构化输出特征（Q1/A1） |
| `POST /api/v1/generate` | 系统工作2 | 基于特征生成模型 SQL、计算路径与模型描述（Q2/A2） |
| `POST /api/v1/register` | 系统工作3 | 注册用户的计算路径到知识库（Q3/A3） |

### 请求/响应示例

```bash
# 系统工作1
curl -X POST http://127.0.0.1:8000/api/v1/parse \
  -H "Content-Type: application/json" \
  -d '{"description": "找最近三个月与多位不同男性同住的年轻女性"}'

# 系统工作2
curl -X POST http://127.0.0.1:8000/api/v1/generate \
  -H "Content-Type: application/json" \
  -d '{"features": [{"dsl": "student.avg_score -> t_student_score.reduce(\u0027student_id\u0027, \u0027avg(score) as avg_score\u0027).avg_score"}], "output_fields": ["student_id","avg_score"]}'

# 系统工作3
curl -X POST http://127.0.0.1:8000/api/v1/register \
  -H "Content-Type: application/json" \
  -d '{"dsl": "student.avg_score -> t_student_score.reduce(\u0027student_id\u0027, \u0027avg(score) as avg_score\u0027).avg_score"}'
```

---

## 特征 DSL 说明

特征 DSL 是描述实体每个维度 SQL 计算逻辑的中间语言，**包含且仅包含该字段的计算逻辑**。
无论该字段在何种情况下与哪些其它信息共同计算，最终提取的特征 DSL 是一致的。

7 个基础算子：

| 算子 | 语法 | 说明 |
|---|---|---|
| `map` | `.map([cal_info])` | 一行进一行出，对应 select 计算列（窗口函数也用 map 表达） |
| `reduce` | `.reduce([key], [cal_info])` | shuffle 聚合，对应 group by |
| `filter` | `.filter([condition])` | 条件过滤，与 SparkSQL 一致 |
| `union` | `.union([table])` | 仅支持两表合并，字段必须能对应；多表需逐级 `.union(t2).union(t3)` |
| `join` | `.join([table], [type], [cond])` | 一次只关联一张表，关联类型与 SparkSQL 一致 |
| `limit` | `.limit([n])` | 取数阶段使用 |
| `sort` | `.sort([expr])` | 取数阶段使用 |

示例：

```
student.avg_score -> t_student.filter('grade=\'2\' and  class=\'3\'').map('score-60 as score').reduce('student_id', 'avg(score) as avg_score').avg_score
gender -> t_student.gender
```

取数逻辑表达式：

```
student.filter("avg_score>60 and birth_year>1999 and gender=1").limit(10)[full_name, avg_score,test_type,count_1]
```

---

## 特征描述结构

标准描述：`业务_主体_限定词组_(维度)属性：值域`

- 业务空间 [D]：如 链家访客（口语中一般缺省）
- 主体 [S]：如 用户、学生
- 限定词组 [C]：如 最近三天、男性（可缺省或多个）
- (维度)属性 [O]：如 访问次数、性别、年龄
- 值域：如 `>5`、`=女`、`>18;<30`

例：`链家访客_用户_最近三天_访问次数：>5` → 主体_属性 = `用户_访问次数`，限定词 = `[最近三天]`。

---

## 字段命名规范

需求要求命名统一使用 **`函数_参数字段`** 规范（`bar_score` 等不规范命名需规范化为 `avg_score`）：

| 场景 | 表达式 | 规范字段名 |
|---|---|---|
| 平均值 | `avg(score)` | `avg_score` |
| 求和 | `sum(amount)` | `sum_amount` |
| 计数 | `count(1)` / `count(*)` | `count_1` |
| 计数 | `count(order_id)` | `count_order_id` |
| 拼接 | `concat(first_name, last_name)` | `concat_first_name_last_name` |
| 原始字段 | `t_student.gender` | `gender`（保持原名） |

规范规则（实现于 `FeatureExtractor.normalize_name`）：
1. 聚合/函数字段名 = `函数名_参数字段`，参数按出现顺序用 `_` 连接，全部小写。
2. `count` 无列名（`count(1)`/`count(*)`）统一为 `count_1`。
3. 常量参数（数字、字符串）转为可读标识，如 `1` → `1`。
4. 原始表字段保持原名，不做改名。
5. 注册服务（系统工作3）在入库时自动校验别名是否符合规范，不符合则重命名为标准名。

---

## 知识库设计（纯文本管理）

需求要求知识库用纯文本管理，方便人工核验后生效；入库前去重校验（MD5）。

### 特征结构库（`data/feature_struct_kb.jsonl`）

```json
{
  "feature_struct": "t_student_score.reduce('student_id', 'avg(score) as avg_score').avg_score",
  "name": "avg_score",
  "rule_trans": "从t_student_score表中按照学生编号分组，对分数取平均值",
  "valid_tans": "",                     // 人工标注内容，首次写入留白
  "table_list": ["t_student_score"],
  "MD5": "0081203dcc68a1cb26b678556684e3e6"
}
```

### 限定词库（`data/qualifier_kb.jsonl`）

```json
{
  "compare_struct": "grade = X",        // 参数已 mask，避免检索受具体参数影响
  "value_type": "string",
  "rule_trans": "年级等于X",
  "valid_tans": "X年级",
  "table": "t_student_score",
  "c_md5": "405e0d0c065d47440780d17bec876fb8"
}
```

### 映射字典（`data/dict/F100.json`）

城市编码等映射编码字典，供 `transValue` 翻译使用（表字段映射信息中 `dict:F100` 引用）。

---

## 规则翻译

### 比较翻译规则（`config/compare_trans_rules.json`）

配置化模板，含三个模板函数：

| 模板函数 | 作用 | 示例 |
|---|---|---|
| `transCol(col0)` | 查询表结构信息翻译列名 | `both_year` → `出生年` |
| `transValue(arg0)` | 按字段翻译补全/映射信息/字典翻译取值 | `2010` → `2010年`；`1`(gender) → `男`；`110000`(city_code) → `北京` |
| `stringFormat(args0)` | 格式化参数列表 | `北京、上海` |

生效示例：

```
待翻译：both_year between 2010 and 2012
规则：  {transCol(col0)}在 {transValue(arg0)} 与 {transValue(arg1)} 之间
结果：  出生年在 2010年 与 2012年 之间
```

### 函数翻译规则（`config/func_trans_rules.json`）

`avg(score)` → `分数的平均值`；`count(1)` → `数量`；`concat(first_name,last_name)` → `姓与名拼接` 等。

### 特征 DSL 规则翻译（辅助打标）

`student.avg_score -> t_student_score.reduce(...)` → `按照学生编号分组，分数的平均值`。

---

## 计算优化

最优计算顺序（需求规定）：**filter → map → union → reduce → join**
（sort / limit 属于取数阶段算子，一般出现在最后，与维度计算无关，不做顺序规约）。

`app/dsl/optimizer.py` 提供 `analyze_swap(a, b)` 可编程安全交换判断与 `optimize()` 优化器，
优化器只执行判定为安全的交换，保证计算结果不变。

### 算子交换分析（任意两个操作交换是否影响计算结果）

下表为任意两个相邻算子交换的安全性结论（A 在前、B 在后交换为 B 在前、A 在后）。

| A \ B | filter | map | union | reduce | join | sort | limit |
|---|---|---|---|---|---|---|---|
| **filter** | ✅ 安全（AND 交换律） | ✅ filter 不依赖 map 产出时安全（先过滤更优）；依赖则不安全 | ⚠️ 需下推至两个分支 | ✅ 仅引用原始字段（WHERE）时安全；引用聚合结果（HAVING）不安全 | ✅ 内连接可下推；外连接右表字段过滤不安全 | ❌ 取数阶段 | ❌ 取数阶段 |
| **map** | ✅ 对称 | ✅ 无字段依赖时安全 | ⚠️ 需保证两分支字段一致 | ✅ 无字段依赖（map 仅基于分组键）时安全；依赖聚合结果不安全 | ✅ map 仅用左表字段时安全 | ❌ | ❌ |
| **union** | ⚠️ filter 需下推至各分支 | ⚠️ map 需下推至各分支 | ✅ 结合律 | ❌ 先 union 再 reduce ≠ 先 reduce 再 union | ❌ | ❌ | ❌ |
| **reduce** | ⚠️ 见上（WHERE/HAVING 语义） | ⚠️ 见上 | ❌ | ❌ 多级聚合层级不同 | ❌ 聚合后 join ≠ join 后聚合 | ❌ | ❌ |
| **join** | ✅ 内连接可下推 | ✅ 无依赖时安全 | ❌ | ❌ | ✅ 无关联字段依赖时安全 | ❌ | ❌ |
| **sort** | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ 多个 sort 可合并 | ❌ 先 limit 再 sort 丢失语义 |
| **limit** | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |

> ✅ 安全（结果不变）｜❌ 不安全（结果可能变化）｜⚠️ 条件安全（满足条件时结果不变）
>
> 说明：sort/limit 属于取数阶段算子，与维度计算算子交换会改变取数结果；本系统默认将 sort/limit 置于 SQL 末尾。

---

## 计算规划

`app/feature/planner.py` 实现：

1. **共同基础检测**：同表 + filter 可合并（求并集去重）+ shuffle 键可合并（相同键，
   或 A 聚合可升级为 A,B 聚合）→ 合并为同一条 SQL。
2. **最优组合**：贪心分组（每组取首个特征作为基准），跨表特征按表分组。
3. **字段依赖分析**：分析每个输出字段依赖的源表字段（`analyze_dependencies`）。
4. **跨表 join 自动补充**：优先使用 `config/join_keys.json` 配置的关联键，
   否则自动匹配两表同名列（如 `student_id`）补充 `left join`。
5. **A 聚合升级**：按 A 聚合可改为按 A,B 聚合后，再基于聚合结果按 A 聚合。

合并示例（需求原文场景）：

```
student.avg_score -> t_student_score.filter('test_type=\'期末\'').reduce('student_id', 'avg(score) as avg_score').avg_score
student.count_1  -> t_student_score.filter('test_type=\'期末\'').reduce('student_id', 'count(1) as count_1').count_1
gender           -> t_student.gender
student_id       -> t_student.student_id
```

合并结果（平均分与考试次数同表、filter 可合并、shuffle 键相同 → 一条 SQL；性别与成绩表不同源 → 单独一条）：

```sql
SELECT student_id, avg(score) as avg_score, count(1) as count_1
FROM t_student_score WHERE (test_type='期末') GROUP BY student_id;

SELECT gender, student_id FROM t_student;
```

---

## LLM / Embedding 配置

`config/settings.yaml` 中 `llm.enabled: false` 时系统离线运行（规则/关键词兜底）。
需要接入大模型时：

```yaml
llm:
  enabled: true
  chat:
    base_url: "https://your-llm-endpoint/v1"   # OpenAI 兼容接口
    api_key: "sk-xxx"
    model: "qwen-plus"
  embedding:
    base_url: "https://your-embed-endpoint/v1"
    api_key: "sk-xxx"
    model: "text-embedding-v3"
```

LLM 用于：特征描述切分（系统工作1）、知识库未命中时的 SQL 生成（场景2）、
候选集语义筛选。Embedding 用于知识库检索相似度计算。

---

## 测试

```bash
python -m pytest tests -v
```

32 个用例覆盖：DSL 解析与 SQL 生成、规则翻译（比较/函数/DSL）、特征提取与命名规范化、
知识库 MD5 去重、注册服务、特征生成、计算规划合并、三个 API 接口。
测试通过 `tests/conftest.py` 将知识库重定向到临时目录，不污染 `data/` 种子数据。

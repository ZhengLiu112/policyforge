# Phase 0 — 数据获取清单

> 全部公开、免注册、无 DUA。预计 60–90 分钟。
> **先下载并验证结构,再写任何解析代码。** 凭想象写 parser 是这个阶段唯一会真正浪费时间的失误。

下载到 `data/raw/`(已在 `.gitignore` 里),抽样后的小文件放 `data/` 下对应子目录并提交。

---

## 1. NCCI PTP Edits — 规则 ground truth

来源:https://www.cms.gov/medicare/coding-billing/national-correct-coding-initiative-ncci-edits/medicare-ncci-procedure-procedure-ptp-edits

下载三样:
- [ ] **Practitioner PTP Edits** 当季完整文件
- [ ] **Practitioner PTP Edits** 上一季完整文件 ← Eval 2 需要
- [ ] **Practitioner Services PTP Quarterly Additions, Deletions, and Revisions** ← 官方变更清单,Eval 2 的答案

> 完整文件行数超过 Excel 的 1,048,576 行上限,CMS 因此把 Practitioner 和 Hospital 文件都拆成了多个分片。**用 pandas 读 txt/csv,不要用 Excel 打开。**

验证:
```python
import pandas as pd
df = pd.read_csv("data/raw/ncci_ptp_current.txt", sep="|", dtype=str, nrows=50)
print(df.columns.tolist()); print(df.head())
```
确认列名(Column 1 / Column 2 code、effective date、deletion date、modifier indicator、PTP edit rationale)与实际分隔符。分隔符可能是 `|`、逗号或定宽,**以实际文件为准**。

活跃边的判定:`deletion date` 为空或 `*`。

---

## 2. NCCI MUE Files — Eval 1 的 Plan B

来源:同上页面左侧导航 "Medically Unlikely Edits"

- [ ] Practitioner Services MUE
- [ ] Outpatient Hospital Services MUE

关键列:HCPCS/CPT 码、MUE 值(单位上限)、**MUE Adjudication Indicator (MAI: 1/2/3)**、**MUE Rationale**(如 Anatomic consideration / Nature of service / CMS policy / Code descriptor)。

Rationale 是官方标注的多分类标签 —— 如果主方案的 MCD 码表结构不好用,就用它做 Eval 1。

---

## 3. NCCI Policy Manual for Medicare — 自然语言输入端

来源:同页面,年度更新的 ZIP,内含分章 PDF。

- [ ] 下载并解压
- [ ] 抽 Chapter 1(General Correct Coding Policies)+ 一个专科章节的文本

```python
import pdfplumber
with pdfplumber.open("data/raw/ncci_manual_ch1.pdf") as pdf:
    text = "\n".join(p.extract_text() or "" for p in pdf.pages)
```
先检查抽出来的文本是否可用(有没有分栏错乱、页眉页脚污染)。需要的话写个简单的清洗函数。这份文本是 L2/L3 检索上下文的语料。

---

## 4. Medicare Coverage Database — Eval 1 主方案 ⭐

来源:https://www.cms.gov/medicare-coverage-database/downloads/downloads.aspx

- [ ] **Current Article Data**(含 Billing & Coding Articles)
- [ ] **Current and Retired Article Data** ← Eval 2 的 LCD 语义 diff 需要
- [ ] **Current LCD Data**

每个包内含:Read Me、数据集(.mdb + .csv)、**Data Dictionary (PDF)**。

### ⚠️ 这一步决定 Eval 1 走主方案还是 Plan B

**先读 Data Dictionary**,确认是否存在把文档关联到码表的关系表(典型命名如 `article_x_hcpc_code`、`article_x_icd10_covered`、`lcd_x_hcpc_code` 一类)。schema 各年有变动,**以随包 Data Dictionary 为准,不要照抄任何猜测的表名。**

- ✅ 存在码表关联 → **主方案**:剥离叙述文本里的码表 → 喂给 LLM → 对照官方码表算 P/R/F1。这是外部客观 ground truth。
- ❌ 不存在或结构不可用 → **立刻切 Plan B**(MUE rationale 分类)。**不要在这里纠结超过 30 分钟。**

挑 2–3 份 Article 做评估集,选择标准:
- 叙述部分明确写出了覆盖条件(诊断限制、频次、修饰符)
- 自带的码表规模适中(几十条,不是几千条)
- 领域好懂(如影像、物理治疗、睡眠检查),便于你在视频里解释

---

## 5. 合成理赔数据

两条路,**先试 A,不顺就用 B**:

**A. CMS DE-SynPUF**
https://data.cms.gov/collection/synthetic-medicare-enrollment-fee-for-service-claims-and-prescription-drug-event
真实感强,但字段是 CMS RIF 格式,需要映射到 `Claim`/`ClaimLine`。

**B. 手工构造 20–40 条**(推荐先做这个)
针对你选的 Article 精确构造:覆盖 hit / miss / 边界 / 修饰符例外四类。
优点:demo 里每条裁决结果你都能当场解释清楚,视频效果更好;也是单元测试的固定夹具。

> 建议 B 做主力,A 作为"可扩展到真实规模数据"的佐证放在 README 里提一句。

---

## 6. 合规检查(提交前必须过)

- [ ] 仓库内**没有 CPT 码描述文本**(码本身可以,AMA 的描述文字不行)
- [ ] 没有 Cotiviti 的 Assessment Instructions 文档(带 © 2026 专有声明)
- [ ] `data/DATA_SOURCES.md` 记录每个文件的来源 URL、下载日期、授权状态
- [ ] 大文件走 `.gitignore`,仓库里只放小样本 + 下载脚本

---

## Phase 0 退出标准

- [ ] `pip install -r requirements.txt` 成功
- [ ] `python3 tests/test_validate.py` → 17 passed
- [ ] `.env` 已配 `OPENAI_API_KEY`,一次 hello-world 调用成功
- [ ] 上述四类数据在 `data/raw/`,列结构已用 pandas 确认
- [ ] **Eval 1 走主方案还是 Plan B —— 已做出决定**
- [ ] 已选定 2–3 份评估用 Article,记在 `eval/gold/README.md`

# 个人热量参考与每日记录

## 热量参考的来源与边界

成人静息能量消耗采用 Mifflin–St Jeor 简化式（kg、cm、周岁）：男性系数 `10W + 6.25H − 5A + 5`，女性系数 `10W + 6.25H − 5A − 161`。不愿选择系数时给两式包络范围，并说明这并非对个人身份的判断；对个体适用性有限。来源：[原论文摘要](https://pubmed.ncbi.nlm.nih.gov/2305711/?dopt=Abstract)。静息值不作为最低或最高摄入标准。

每日维持能量 = 静息值 × 活动系数。脚本以 `sedentary` 1.4–1.6（久坐、少活动）、`light` 1.6–1.8（轻活动）、`active` 1.8–2.0（经常活动）、`very_active` 2.0–2.4（体力活动多）生成**起始情景范围**。这些分组边界是本工具的保守工作约定，不是原论文的分组或经验证的置信区间。活动量要结合工作、通勤及运动综合判断；无法匹配时先补问，不假装精确。[NIDDK 规划器](https://www.niddk.nih.gov/bwp) 说明常见活动水平约 1.4–2.5，久坐人群也可能被高估。这里不是 NIDDK 的动态体重模型，不能推算减重期限。

仅自动为完成筛查的普通成人估算维持参考；原研究年龄为 19–78 岁，超出此范围先采用记录模式或已有专业计划。未满 18、孕哺期、特殊饮食疾病、进食障碍照护等不自动生成限额。`special_care` 未回答不能当作 false；资料缺项先记录食物，等补齐再计算。

目标 `maintain/lose/gain` 都可记录。除维持外，若无已适用的个人方案，继续显示“维持体重参考，目标计划待确定”，不自行减去固定值。有用户明确提供的个人计划，用 `target_override` 存储目标中心、上下限及来源说明。低于 1200 kcal/天的范围不在本工具中作为扣减额度启用；这是工具限制，不是适合所有人的医学最低值，不能据此推荐 1200。BMI <18.5 时不启用减重额度。特殊照护模式只记录，不做超额判断。

## 本地状态与命令

仅 Python 标准库，路径使用实际绝对路径。状态保存在当前聊天专用随机目录，包含档案与日志；不要打包、分享此文件或写到共享 skill 目录。卡片和摘要只包含热量参考，不展示身高体重与健康筛查。新聊天不能靠技能本身找回旧日志，应由用户给出旧状态路径或重新建档。档案可另存 `allergies`、`preferences` 字符串数组用于助手生成建议。以下为虚构资料格式，不能当成用户资料。

```json
{
  "operation": "init",
  "timezone_offset": "+08:00",
  "timezone_name": "Asia/Shanghai",
  "is_demo": true,
  "profile": {
    "age": 30, "height_cm": 175, "weight_kg": 70,
    "sex_coefficient": "male", "activity": "light", "goal": "maintain",
    "special_care": false
  }
}
```

`profile` 可设为 null，表示用户跳过资料。可选 `target_override` 放在 profile 中：`{"kcal":2200,"low":2100,"high":2300,"basis":"用户提供的当前适用个人计划"}`。档案数据必须由用户提供，`is_demo` 真实日志应为 false。时区偏移来自当前用户时区，夏令时地区要更新偏移；实际日期必须明确传入，避免借用宿主时区。用户拒绝本地保存时，以聊天内表格手动记账，不运行写入操作。

```text
python /skill/scripts/daily_log.py --state /chat/work/food-photo-card-state.json --request /chat/work/request.json --output /chat/work/daily-summary.json
```

请求格式（所有 `date` 为用户当地实际食用日 `YYYY-MM-DD`）：

- `init`：上例；已有文件时拒绝覆盖。
- `set-profile`：`profile` 为完整新档案或 null；已创建日期保留原参考快照，新建日期采用新档案。
- `retarget`：`date`，明确将当前新档案的参考用于该日；不会改旧餐次的食物数值。
- `add`：`date`, `id`（稳定餐次 ID）, `meal_path`（本餐 JSON 路径）, `photo_paths`（本地照片路径数组）, `fraction`（0–1，默认 1）, `provisional`（默认 true）, `new_occurrence`（默认 false）。相同 ID 不会重复添加；同日相同照片默认拒绝重复。用户明确又吃了一份才设 `new_occurrence: true`。相同食物重拍但哈希不同仍需助手语义防重。
- `amend`：`date`, `id`，可更新 `fraction` / `provisional` / `meal_path`；从原始整份数据重算，不在上次缩放结果上再次缩放。
- `remove`：`date`, `id`；保留撤销状态以便追溯，撤销后不再累计。
- `complete`：`date`, `complete`（true/false）；仅用户确认全天已记全后设 true；有新增或修正时自动回到 false。
- `summary`：`date`；只读。日期无记录时显示空白日、不创建或擦除其他日期。
- `export-meal`：`date`, `id`, `output_path`；将该餐按食用比例缩放后的 JSON 导出，用来渲染卡片，避免本餐数字与日累计不一致。

写操作原子保存，无自动上传。脚本打印并可写出该日摘要。`init`、`set-profile` 可以附 `date` 来输出指定日期摘要。日期未传时仅这两种操作用已保存时区的当前日期；其他操作必须明确传日。

对于按图中整份构建的 meal JSON，`fraction` 填实际吃的比例。若 meal JSON 已只描述实际吃掉的部分（例如拍照时剩余烧饼），`fraction` 应为 1，不再次扣减。`amend` 与 `remove` 重新累计，参考余额不滚存，日总计不包含已撤销条目。

## 摘要与卡片

摘要为 `date`, `is_demo`, `complete`, `count`, `provisional_count`, `target`（可为 null）, `consumed`（`kcal/low/high`）, `remaining`（可为 null；同三字段，允许负数）, `comparison`, `status`, `records`。

- `target` 来自当日快照：中心为参考范围中点；范围不是统计置信区间。
- `consumed` 为有效餐次的缩放后原始值之和，不反复对显示值求和。
- `remaining.kcal = target.kcal − consumed.kcal`；剩余范围为 `[target.low − consumed.high, target.high − consumed.low]`。
- `comparison`: `above` 仅当摄入下界高于参考上界；`below` 仅当摄入上界低于参考下界；其余 `overlap`。无目标为 `unavailable`。
- `status` 同时说明是否完整、是否有暂估；即使显示中心差额，也不能把单点当作确定是否超出。

用 `render_card.py meal.json --output card.html --daily-json daily-summary.json --photo food.jpg` 生成带每日进度的卡片。分享内容只使用摘要，不把状态文件嵌入 HTML。建议由助手根据完整的餐次内容、忌口和下一餐情境撰写，脚本不会仅按余额大小机械推荐食物。

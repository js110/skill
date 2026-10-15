# 卡片数据

Python 3 标准库即可运行。相对路径以调用者工作目录为准；照片只接受本地 JPEG、PNG、WebP、GIF，内嵌到 HTML。不要把图片上传到外部服务。中文 JSON 保存为 UTF-8。

```json
{
  "title": "鸡肉蔬菜饭",
  "meal_label": "这一餐",
  "is_demo": false,
  "portion_basis": "按图中整份估算，实际吃掉多少尚未确认",
  "confidence": "份量把握有限",
  "items": [
    {
      "name": "熟米饭",
      "portion": "约 150g，按可食熟重",
      "kcal": 195,
      "low": 150,
      "high": 240,
      "basis": "示范字段；实际任务填重量 × kcal/100g 和来源状态，不可照抄本数值"
    }
  ],
  "comment": "这一餐，认真吃饭的快乐有了。",
  "tip": "下一餐可以搭配鱼或豆腐和蔬菜。",
  "assumptions": ["份量按容器比例目测", "用油量不能从照片精确判断"],
  "sources": [
    {"label": "实际查阅的食物条目名称和状态", "url": "https://example.org/replace-with-verified-entry"}
  ]
}
```

字段说明：`title`、`portion_basis`、`confidence`、`items`、`comment`、`tip`、`assumptions`、`sources` 必填；`meal_label` 默认“这一餐”，`is_demo` 默认 false。`items` 至少一项；每项需 `name`、`portion`、`kcal`、`low`、`high`、`basis`，数值有限且满足 `0 ≤ low ≤ kcal ≤ high`，单位统一 kcal。输入的是**用户实际吃的份量**对应数值；食用比例应先应用到各项，再传入脚本。

`confidence` 是文字描述（如“份量把握有限”“有标签和实测重量”），不是未经校准的概率。`assumptions` 列出关键估算假设；`sources` 可为空数组，此时卡片显示“经验估算，未核对数据库”。无来源时 `basis` 也应明确是经验估计。有来源时逐项说明对应来源和计算，以便追溯。

`is_demo: true` 会在卡片顶部和底部显示演示标记。示例数值只用来测试展示；没有真实图片不能去掉标记。脚本汇总原始数值后将显示值取整到十位，因此细项显示和总计可能有微小取整差异，卡片有说明。原始数据保留在 JSON。

运行（用当前环境的 Python 可执行路径替代 `python`，用实际绝对路径替代下列路径）：

```text
python /path/to/food-photo-card/scripts/render_card.py /path/to/meal.json --output /path/to/meal-card.html --photo /path/to/food.jpg
```

HTML 是结果卡片，不是上传识别的独立应用。需要重新分析或调整份量时，由助手改 JSON 并重新运行。脚本不会从 HTML 中收集个人信息或发起网络请求。

日记录模式：读取 [daily-tracking.md](daily-tracking.md)，先用 `daily_log.py` 写入日志并输出日汇总 JSON，再用 `export-meal` 导出食用比例校正后的本餐 JSON。渲染时添加 `--daily-json /path/to/daily-summary.json`；也支持在 meal JSON 的 `daily` 字段传同样摘要。卡片会增加每日参考、已记录、剩余参照、估算范围、记录完整性和参考依据；没有个人资料时显示记录模式。原有单餐 JSON 和命令保持兼容。

只发图模式：按 [image-first.md](image-first.md) 生成观察 JSON，用 `estimate_meal.py` 自动计算并添加 `assessment`。其必需字段为 `method`、`note`（字符串），`observations`、`uncertain`（字符串数组）；它区分已查的食物数据与推测的份量/配方，卡片的说明不代表实测准确率。旧单餐 JSON 仍兼容。

# FineVision 本地展示文档

这是一个依赖为零的单页工程手册，正文以当前仓库实现和冻结测评产物为准。

章节结构：

1. 展示导航与页面路由
2. 项目介绍
3. 操作手册
4. 项目实现细节
5. 评估测试报告
6. 附录：工程参考手册

AI 标注不使用独立文档子路由，而是同时纳入产品路由表、操作手册、实现细节、评估协议和附录。

## 构建与预览

```bash
python3 showcase/build.py
python3 -m http.server 5180 --directory showcase/site
```

打开 <http://localhost:5180/>。

## 验证

```bash
python3 showcase/check.py
PLAYWRIGHT_MODULE=/path/to/node_modules/playwright node showcase/smoke.cjs
```

`build.py` 每次会清空并重建 `showcase/site`，因此旧的构建产物不会残留。评估章节包含 48 单元完整矩阵的汇总结果、调用稳定性、工具执行统计和结论边界，不包含原始受版权约束的数据集图片或 API 密钥。

# 采购合规审查演示 Demo

这是一个可直接运行在本地 Semantica Explorer 中的业务演示。故事主线是：一笔 268 万元的服务器采购触发围标、关联方未申报、采购方式不合规和供应商资质过期四类规则，系统将采购暂缓并升级到集团合规委员会复核。

演示数据均为虚构，不对应真实企业或个人。

## 一键准备

在仓库根目录执行：

```bash
./demos/procurement-compliance/scripts/reset-and-load-demo.sh
```

然后打开 [http://127.0.0.1:8000/](http://127.0.0.1:8000/)。

初始化脚本会重启 `explorer` 容器，从而清空 Explorer 当前的内存图谱和本体注册表，再依次导入：

- 采购合规业务图谱：36 个节点、54 条关系；
- SKOS 采购合规术语表：14 个节点、29 条关系；
- OWL 采购合规本体：24 个节点、31 条关系。

最终环境应为 76 个节点、114 条关系。脚本会自动验证服务健康、决策案例、违规命中、路径、语义邻域、时间范围、词表和本体。

重置只影响 Explorer 的内存状态，不会删除 FalkorDB 容器、数据卷或其他 Docker 项目。

## 文件说明

| 文件 | 用途 |
| --- | --- |
| `data/procurement-compliance-base.json` | 主演示图谱，包含高风险、合规和历史先例三个决策案例 |
| `data/live-blacklist-update.json` | 现场增量文件；请在演示“实时更新”环节通过界面导入，不要预先加载 |
| `data/procurement-compliance-vocabulary.ttl` | SKOS 风险、采购方式和审查结论术语体系 |
| `data/procurement-compliance-ontology.ttl` | OWL 采购合规领域模型 |
| `scripts/load-demo.sh` | 向当前运行中的 Explorer 追加加载 Demo |
| `scripts/reset-and-load-demo.sh` | 清空 Explorer 内存状态后重新加载 Demo |
| `scripts/verify-demo.py` | 只读验证当前演示环境 |
| `采购合规审查演示脚本.md` | 现场逐步操作和逐句讲解稿 |
| `采购合规审查Demo输入输出与源码解读.md` | 按演示步骤解释输入、输出、源码实现、配置方式和 Python 调用示例 |

## 单独验证

```bash
./demos/procurement-compliance/scripts/verify-demo.py
```

也可以指定其他本地地址：

```bash
./demos/procurement-compliance/scripts/verify-demo.py http://127.0.0.1:8000
```

## 演示后恢复

如果现场执行过推理、增量导入或实体合并，再次运行下面的命令即可恢复标准起点：

```bash
./demos/procurement-compliance/scripts/reset-and-load-demo.sh
```

## 演示范围说明

本 Demo 展示的是 Semantica 的知识图谱浏览、路径分析、时间语义、决策上下文、规则推理、导入导出、实体消歧、审计日志、PROV-O 血缘、SKOS 词表和 OWL 本体治理能力。它不是已经接入企业 ERP、SRM、工商或监管系统的成品采购应用；生产落地时需要再对接真实数据源、身份权限、持久化存储和企业规则库。

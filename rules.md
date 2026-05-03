# Abaqus 建模脚本规范

> 所有 AI 生成的 Abaqus 脚本必须遵守以下规则，否则大概率运行失败。

## 禁止清单

| 禁止 | 原因 | 正确做法 |
|------|------|---------|
| `vertices[0]` / `faces[0]` 索引选几何 | 索引顺序不可靠 | `findAt((坐标,))` |
| `m.BeamProfile(...)` | API 不存在 | `m.RectangularProfile(...)` 或用实体截面 |
| `BaseWire` + 线单元 | API 复杂且版本敏感 | `BaseSolidExtrude` + 实体截面 |
| 省略 `setMeshControls` | 可能生成错误单元类型 | 显式指定 `HEX` 或 `TET` |
| 省略 `setElementType` | 依赖默认值不可控 | 显式指定 `C3D8R` / `C3D4` / `S4R` |
| 省略 `generateMesh` | 网格不生成 | 显式调用 |
| 省略 `DatumCsysByDefault` | 坐标系可能不一致 | 必须显式定义 |
| 单点集中力作用在实体面 | 应力奇异 | 均分到面节点 |
| 不删除同名旧模型 | `NameError` | `if name in mdb.models: del mdb.models[name]` |
| `io.open('w', encoding='utf-8')` + `json.dump()` | Python 2.7 类型冲突 | `open('w')` + `json.dumps()` |
| `os.replace()` | Python 2.7 不存在 | `os.remove()` + `os.rename()` |
| `unicode()` | Python 3 不存在 | `str.encode('utf-8')` + `open('wb')` |

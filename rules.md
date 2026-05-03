# Abaqus 建模脚本规范

> 所有 AI 生成的 Abaqus 脚本必须遵守以下规则，否则大概率运行失败。

---

## 1. 脚本结构：固定七段式

所有案例遵循完全相同的代码结构，不得省略任何一段：

```
① 导入 & 常量定义
② 参数加载（带边界校验）
③ 模型清理（删除同名旧模型/旧 Job）
④ 几何 → 材料 → 截面 → 赋截面
⑤ Set 定义 → 网格控制 → 生成网格
⑥ 装配 → 坐标系 → Step → BC → Load → Job
⑦ 提交求解 → 提取结果
```

### 1.1 导入模板

```python
from __future__ import print_function
import json, math, os, sys
from abaqus import mdb
from abaqusConstants import (
    C3D8R, ANALYSIS, CARTESIAN, DEFAULT, DEFORMABLE_BODY,
    FROM_SECTION, HEX, MIDDLE_SURFACE, NODAL, OFF, ON,
    PERCENTAGE, SINGLE, STANDARD, THREE_D, UNIFORM,
)
import mesh
import regionToolset
from odbAccess import openOdb
```

**规则：**
- 必须写 `from __future__ import print_function`，Abaqus 内置 Python 2.7 需要。
- `from abaqusConstants import *` 仅在显式动力学案例中使用（因为需要大量常量如 `JOHNSON_COOK`, `HARD`, `PENALTY` 等），静力/模态案例应显式列出所需常量。
- `openOdb` 在显式动力学案例中用 `try/except` 包裹，因为求解可能失败。

### 1.2 常量命名规范

```python
MODEL_NAME = "TextToCAE_Cantilever_Model"
JOB_NAME = "TextToCAE_Cantilever"
LENGTH_MM = 120.0
WIDTH_MM = 20.0
YOUNGS_MODULUS_MPA = 210000.0
SEED_SIZE_MM = 6.0
```

**规则：**
- 物理量必须带单位后缀：`_MM`, `_MPA`, `_N`, `_MPS`, `_S`, `_RPM`。
- 常量名全大写 + 下划线。
- 所有尺寸、材料参数在脚本顶部集中定义，不得硬编码在函数体内。

---

## 2. 模型清理：必须先删后建

```python
def build_model():
    if MODEL_NAME in mdb.models:
        del mdb.models[MODEL_NAME]
    if JOB_NAME in mdb.jobs:
        del mdb.jobs[JOB_NAME]
    model = mdb.Model(name=MODEL_NAME)
```

**规则：**
- 必须在创建 Model 之前检查并删除同名模型。
- 必须在创建 Job 之前检查并删除同名 Job（部分案例在 `build_model()` 末尾创建 Job，此时旧 Job 也需删除）。
- 不做清理会导致 `NameError: Model already exists` 或 Job 覆盖失败。

---

## 3. 几何建模

### 3.1 实体建模：`BaseSolidExtrude`（首选）

```python
sketch = model.ConstrainedSketch(name="beam_profile", sheetSize=240.0)
sketch.rectangle(point1=(0.0, 0.0), point2=(WIDTH_MM, HEIGHT_MM))
part = model.Part(name="Beam", dimensionality=THREE_D, type=DEFORMABLE_BODY)
part.BaseSolidExtrude(sketch=sketch, depth=LENGTH_MM)
del model.sketches["beam_profile"]
```

**规则：**
- `sheetSize` 应 ≥ 模型最大尺寸的 2 倍。
- `BaseSolidExtrude` 后必须删除草图：`del model.sketches["beam_profile"]`。
- 坐标从 `(0, 0)` 开始，不要从负坐标开始（悬臂梁案例），除非是对称模型（球冲击案例从中心开始）。

### 3.2 旋转体：`BaseSolidRevolve`

```python
sketch.ConstructionLine(point1=(0.0, -R), point2=(0.0, R))
sketch.ArcByCenterEnds(center=(0.0, 0.0), point1=(0.0, -R), point2=(0.0, R), direction=CLOCKWISE)
sketch.Line(point1=(0.0, R), point2=(0.0, -R))
part.BaseSolidRevolve(sketch=sketch, angle=360.0, flipRevolveDirection=OFF)
```

**规则：**
- 旋转体必须画构造线（`ConstructionLine`）作为旋转轴。
- 半截面轮廓必须闭合（最后一条线连回起点）。
- `angle=360.0` 生成完整旋转体。

### 3.3 壳体建模：`BaseShell`

```python
sketch.rectangle(point1=(-L/2, -W/2), point2=(L/2, W/2))
part.BaseShell(sketch=sketch)
```

**规则：**
- 壳体用 `BaseShell`，不用 `BaseSolidExtrude` + 薄厚度。
- 壳体截面用 `HomogeneousShellSection`，不用 `HomogeneousSolidSection`。

### 3.4 带孔平板

```python
sketch.rectangle(point1=(0.0, 0.0), point2=(LENGTH_MM, WIDTH_MM))
sketch.CircleByCenterPerimeter(
    center=(LENGTH_MM / 2.0, WIDTH_MM / 2.0),
    point1=(LENGTH_MM / 2.0 + HOLE_RADIUS_MM, WIDTH_MM / 2.0),
)
```

**规则：**
- 先画外轮廓矩形，再画内圆孔。Abaqus 自动识别为带孔截面。
- `CircleByCenterPerimeter` 的 `point1` 是圆周上任意一点（用于确定半径）。

---

## 4. 材料与截面

### 4.1 弹性材料（静力/模态）

```python
material = model.Material(name="Steel")
material.Elastic(table=((YOUNGS_MODULUS_MPA, POISSONS_RATIO),))
```

### 4.2 含密度材料（模态/显式动力学）

```python
material = model.Material(name="Steel")
material.Density(table=((7.85e-9,),))  # tonne/mm³
material.Elastic(table=((YOUNGS_MODULUS_MPA, POISSONS_RATIO),))
```

**规则：**
- 模态分析和显式动力学必须定义 `Density`，否则无法求解。
- 密度单位：tonne/mm³（钢 = 7.85e-9，铝 = 2.81e-9）。
- `Density` 必须在 `Elastic` 之前调用。

### 4.3 Johnson-Cook 塑性材料（冲击/切削）

```python
material.Plastic(hardening=JOHNSON_COOK, table=((792.0, 510.0, 0.26, 1.03, 1793.0, 293.0),))
material.RateDependent(type=JOHNSON_COOK, table=((0.014, 1.0),))
```

**规则：**
- Johnson-Cook 必须用 `try/except` 包裹，因为参数格式在不同 Abaqus 版本间有差异。
- 失败时回退到简单 `Plastic` 表格：`material.Plastic(table=((900.0, 0.0), (1150.0, 0.06), ...))`。

### 4.4 截面赋值

```python
model.HomogeneousSolidSection(name="SteelSection", material="Steel", thickness=None)
part.SectionAssignment(
    region=regionToolset.Region(cells=part.cells),
    sectionName="SteelSection",
    offset=0.0,
    offsetType=MIDDLE_SURFACE,
    offsetField="",
    thicknessAssignment=FROM_SECTION,
)
```

**规则：**
- 实体用 `HomogeneousSolidSection`，`thickness=None`。
- 壳体用 `HomogeneousShellSection`，`thickness=THICKNESS_MM`。
- `region=regionToolset.Region(cells=part.cells)` 赋给所有单元。
- `offsetType=MIDDLE_SURFACE`, `thicknessAssignment=FROM_SECTION` 是标准写法。

---

## 5. Set 定义与几何选择

### 5.1 按坐标选面：`findAt()`

```python
part.Set(faces=face_at_z(part, z=0.0), name="fixed_end")

def face_at_z(part, z=None):
    return part.faces.findAt(((WIDTH_MM / 2.0, HEIGHT_MM / 2.0, z),))
```

**规则：**
- **禁止**用索引选面（`faces[0]`, `faces[1]`），索引顺序不可靠。
- `findAt()` 参数是嵌套元组：`((x, y, z),)`，注意双层括号。
- 坐标选面的中心点：`WIDTH/2, HEIGHT/2, z`，即面的几何中心。

### 5.2 按包围盒选节点：`getByBoundingBox()`

```python
free_end_nodes = instance.nodes.getByBoundingBox(
    xMin=-1.0e-6, xMax=WIDTH_MM + 1.0e-6,
    yMin=-1.0e-6, yMax=HEIGHT_MM + 1.0e-6,
    zMin=LENGTH_MM - 1.0e-6, zMax=LENGTH_MM + 1.0e-6,
)
assembly.Set(name="free_end_nodes", nodes=free_end_nodes)
```

**规则：**
- 容差统一用 `1.0e-6`（mm），不要用 0.0。
- 必须检查结果是否为空：`if len(free_end_nodes) == 0: raise RuntimeError(...)`。
- 用于选择面上的所有节点，以便分布载荷。

### 5.3 按节点标签选节点：`sequenceFromLabels()`

```python
fixed_labels = []
for node in instance.nodes:
    x, y, z = node.coordinates
    if abs(x - EDGE) <= tolerance:
        fixed_labels.append(node.label)
assembly.Set(name="fixed", nodes=instance.nodes.sequenceFromLabels(tuple(sorted(set(fixed_labels)))))
```

**规则：**
- 用于复杂选择逻辑（如选边界上所有节点）。
- 标签必须去重排序：`tuple(sorted(set(labels)))`。
- 容差用 `max(SEED_SIZE * 0.55, 0.05)`，与网格尺寸成比例。

### 5.4 Part 级 vs Assembly 级 Set

```python
# Part 级 Set（网格划分前定义）
part.Set(faces=face_at_z(part, z=0.0), name="fixed_end")

# Assembly 级 Set（网格划分后定义）
assembly.Set(name="fixed_end", faces=instance.faces.findAt(((W/2, H/2, 0.0),)))
```

**规则：**
- Part 级 Set 在 `generateMesh()` 之前定义，会自动映射到 Instance。
- Assembly 级 Set 在 `Instance` 创建后定义，用 `instance.faces.findAt()` 选择。
- 两者都可以用于 BC/Load，但 Assembly 级 Set 更可靠（因为 instance 上的面编号确定）。

---

## 6. 网格控制

### 6.1 六面体网格（规则几何）

```python
part.seedPart(size=SEED_SIZE_MM, deviationFactor=0.1, minSizeFactor=0.1)
part.setMeshControls(regions=part.cells, elemShape=HEX)
elem_type = mesh.ElemType(elemCode=C3D8R, elemLibrary=STANDARD)
part.setElementType(regions=(part.cells,), elemTypes=(elem_type,))
part.generateMesh()
```

### 6.2 四面体网格（复杂几何，如带孔）

```python
part.seedPart(size=SEED_SIZE_MM, deviationFactor=0.1, minSizeFactor=0.1)
part.setMeshControls(regions=part.cells, elemShape=TET, technique=FREE)
elem_type = mesh.ElemType(elemCode=C3D4, elemLibrary=STANDARD)
part.setElementType(regions=(part.cells,), elemTypes=(elem_type,))
part.generateMesh()
```

### 6.3 壳单元

```python
part.seedPart(size=SEED_SIZE_MM, deviationFactor=0.1, minSizeFactor=0.1)
elem_type = mesh.ElemType(elemCode=S4R, elemLibrary=EXPLICIT)
part.setElementType(regions=(part.faces,), elemTypes=(elem_type,))
part.generateMesh()
```

**规则：**
- **禁止**省略 `setMeshControls`、`setElementType`、`generateMesh` 中的任何一个。
- 规则几何（矩形、长方体）用 `HEX` + `C3D8R`。
- 复杂几何（带孔、旋转体）用 `TET` + `C3D4`，必须加 `technique=FREE`。
- 显式动力学用 `elemLibrary=EXPLICIT`，静力/模态用 `STANDARD`。
- 壳单元用 `S4R`，`regions=(part.faces,)`（不是 `part.cells`）。
- `deviationFactor=0.1, minSizeFactor=0.1` 是标准值，显式动力学可用 `0.08`。
- `setMeshControls` 可能失败（如旋转体），用 `try/except` 包裹。

---

## 7. 装配与坐标系

```python
assembly = model.rootAssembly
assembly.DatumCsysByDefault(CARTESIAN)
instance = assembly.Instance(name="Beam-1", part=part, dependent=ON)
```

**规则：**
- **必须**在创建 Instance 之前调用 `DatumCsysByDefault(CARTESIAN)`。
- Instance 命名规则：`{Part名}-1`（Abaqus 自动命名）。
- `dependent=ON` 是标准选择（依赖实例，与 Part 共享网格）。
- 如需平移/旋转实例：
  ```python
  assembly.translate(instanceList=("Plate-1",), vector=(0.0, 0.0, -0.5 * thickness))
  assembly.rotate(instanceList=("Projectile-1",), axisPoint=(0,0,0), axisDirection=(1,0,0), angle=-90.0)
  ```

---

## 8. 分析步与载荷

### 8.1 静力分析

```python
model.StaticStep(name="Load", previous="Initial")
model.EncastreBC(name="Fixed", createStepName="Initial", region=assembly.sets["fixed_end"])
model.ConcentratedForce(
    name="TipForce", createStepName="Load",
    region=assembly.sets["free_end_nodes"],
    cf2=TIP_FORCE_N / float(len(free_end_nodes)),
)
```

### 8.2 模态分析

```python
model.FrequencyStep(name="Modal", previous="Initial", numEigen=6)
model.EncastreBC(name="FixedLeft", createStepName="Initial", region=assembly.sets["left_face"])
```

### 8.3 显式动力学

```python
model.ExplicitDynamicsStep(name="Impact", previous="Initial", timePeriod=0.0025, improvedDtMethod=ON)
model.Velocity(
    name="SphereInitialVelocity", createStepName="Impact",
    region=assembly.sets["sphere_nodes"],
    velocity1=0.0, velocity2=0.0, velocity3=-IMPACT_VELOCITY_MPS * 1000.0,
    omega=0.0,
)
```

### 8.4 接触（显式动力学）

```python
model.ContactProperty("ContactProp")
model.interactionProperties["ContactProp"].NormalBehavior(pressureOverclosure=HARD, allowSeparation=ON, constraintEnforcementMethod=DEFAULT)
model.interactionProperties["ContactProp"].TangentialBehavior(formulation=FRICTIONLESS)
model.ContactExp(name="GeneralContact", createStepName="Impact")
model.interactions["GeneralContact"].includedPairs.setValuesInStep(stepName="Impact", useAllstar=ON)
model.interactions["GeneralContact"].contactPropertyAssignments.appendInStep(
    stepName="Impact", assignments=((GLOBAL_SELF, "ContactProp"),),
)
```

**规则：**
- 集中力必须均分到面节点：`cf2 = TOTAL_FORCE / float(len(nodes))`，禁止单点加载。
- 位移边界条件用 `DisplacementBC`，未约束分量设为 `UNSET`。
- 显式动力学中速度单位是 mm/s，需要 `m/s * 1000`。
- 接触属性和接触定义都用 `try/except` 包裹。
- `improvedDtMethod=ON` 提高显式步的稳定性。

---

## 9. Job 创建

```python
mdb.Job(
    name=JOB_NAME,
    model=MODEL_NAME,
    description="...",
    type=ANALYSIS,
    atTime=None, waitMinutes=0, waitHours=0, queue=None,
    memory=90, memoryUnits=PERCENTAGE, getMemoryFromAnalysis=True,
    explicitPrecision=SINGLE, nodalOutputPrecision=SINGLE,
    echoPrint=OFF, modelPrint=OFF, contactPrint=OFF, historyPrint=OFF,
    userSubroutine="", scratch="", resultsFormat=ODB,
)
```

**规则：**
- 以上参数是完整模板，不得省略任何一项。
- `memory=90, memoryUnits=PERCENTAGE` 是标准设置。
- `resultsFormat=ODB` 确保输出 ODB 文件。

---

## 10. 提交与结果提取

```python
def main():
    os.chdir(ROOT)
    job = mdb.jobs[JOB_NAME]
    job.submit(consistencyChecking=OFF)
    job.waitForCompletion()
    extract_results()
```

**规则：**
- `os.chdir(ROOT)` 确保工作目录正确。
- `consistencyChecking=OFF` 跳过一致性检查，加速提交。
- `waitForCompletion()` 阻塞直到求解完成。
- 结果提取必须用 `try/finally` 包裹 `openOdb`，确保关闭 ODB：

```python
odb = openOdb(path=odb_path, readOnly=True)
try:
    frame = odb.steps["Load"].frames[-1]
    stress = frame.fieldOutputs["S"]
    ...
finally:
    odb.close()
```

---

## 11. 参数校验（可复用模板）

```python
DEFAULT_PARAMETERS = {
    "length_mm": 120.0,
    "width_mm": 60.0,
    ...
}

def parameter_number(parameters, name, minimum, maximum):
    try:
        value = float(parameters.get(name, DEFAULT_PARAMETERS[name]))
    except Exception:
        value = float(DEFAULT_PARAMETERS[name])
    return min(max(value, minimum), maximum)

def load_parameters():
    parameters = dict(DEFAULT_PARAMETERS)
    if os.path.exists(PARAMETERS_PATH):
        with open(PARAMETERS_PATH, "r") as handle:
            loaded = json.load(handle)
        if isinstance(loaded, dict):
            parameters.update(loaded)
    return {
        "length_mm": parameter_number(parameters, "length_mm", 40.0, 300.0),
        ...
    }
```

**规则：**
- 所有外部参数必须有默认值（`DEFAULT_PARAMETERS`）。
- 所有参数必须有上下界校验（`min(max(value, lower), upper)`）。
- JSON 加载必须用 `try/except` 包裹，文件可能不存在或格式错误。

---

## 12. 禁止清单

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

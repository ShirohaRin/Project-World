**Python 现代语法：从竞赛写法到工程写法**

本文整理 Project IDEA 生物方法模块代码里出现的现代 Python 写法。定位是"读工程代码时遇到不认识的东西来查"，所以每个点都配了实测结果和踩坑记录，而不是只给语法定义。

---

# 一，类型标注

## 1. 基本形式

```python
x: int                                   # 变量标注
def f(a: int, b: str) -> list[float]:    # 参数与返回值标注
    ...
```

Python 3.5 引入，3.6 起成为惯例，3.9 起内置容器可以直接泛型化。

## 2. 关键前提：运行时完全不生效

这是最容易误解的一点。**类型标注只是给静态检查器和编辑器看的注解，解释器根本不管。**

```python
def f(a: int) -> str:
    return a                 # 返回 int，Python 不报错

f("随便一个字符串")            # 照样跑
```

所以下面两件事必须分开理解：

| 手段 | 生效时机 | 负责什么 | 效果 |
|---|---|---|---|
| 类型标注 | 静态检查（mypy / pyright / IDE） | 提前发现笔误 | 编辑器里标红，但程序照跑 |
| `raise` 校验 | 运行时 | 兜住一切外部输入 | 真的中断执行 |

**工程代码里两者必须同时存在。** 只写标注，等于没防；只写校验，写代码时没有提示。

以 PCA 为例：

```python
Transform = Literal["none", "log1p", "center", "zscore"]   # 静态层

def fit_pca(..., transform: Transform = "center"): ...

# 运行时层
if transform not in {"none", "log1p", "center", "zscore"}:
    raise ValueError(f"不支持的变换方式：{transform}")
```

写 `transform="centre"`（拼错）时：编辑器立刻标红；万一从配置文件或 HTTP 请求传进来，运行时的 `raise` 接手。

## 3. `Literal`：把取值限定住

```python
from typing import Literal

Transform = Literal["none", "log1p", "center", "zscore"]
```

含义：**取值只能是这几个字面量之一**。

看起来像普通赋值，实际上左边那行定义的是**类型别名**——`Transform` 这个名字代表的是类型，不是值。可以在参数标注里直接当类型用：

```python
def fit_pca(matrix, transform: Transform = "center") -> PCAResult: ...
```

**它同样只是静态约束**，所以必须配运行时校验，理由同上。

## 4. 联合类型：`int | None`

```python
n_components: int | None = None
```

PEP 604 写法（Python 3.10+）。对照：

| 写法 | 版本要求 | 需要 import |
|---|---|---|
| `int \| None` | 3.10+ | 无 |
| `Optional[int]` | 任意 | `from typing import Optional` |
| `Union[int, str]` | 任意 | `from typing import Union` |

`int | str | None` 可以一直串下去，比 `Union[int, str, None]` 干净。

**为什么默认值用 `None`**：`None` 的语义就是"没有指定"，不会和任何合法取值撞车。比用 `-1`、`0`、`""` 这类魔法值当哨兵清楚得多——那种写法会让读者分不清"这是默认值"还是"这是真的传了个 0"。

## 5. 容器类型标注

```python
list[int]                      # 整数列表
dict[str, float]               # 键 str、值 float 的字典
tuple[str, ...]                # 任意长度的字符串元组
tuple[int, str]                # 恰好两个元素，分别是 int 和 str
set[str]                       # 字符串集合
np.ndarray                     # 不指定形状和 dtype
```

3.9 之后 `list[int]` 可以直接写；之前必须 `from typing import List` 再写 `List[int]`。

**实际工程里更推荐用抽象类型**，因为它表达"我需要的能力"而不是"具体实现"：

```python
from collections.abc import Sequence, Iterable, Mapping

def load_metadata(path, ids: Sequence[str]) -> None:
    ...    # 只要求“能按顺序取”，不要求一定是 list
```

对照表：

| 抽象类型 | 含义 | 好处 |
|---|---|---|
| `Sequence[T]` | 有顺序、可索引、可 `len()` | 传 list 和 tuple 都行 |
| `Iterable[T]` | 只能迭代一次 | 传生成器也行 |
| `Mapping[K, V]` | 只读字典 | 传 dict 和自定义映射都行 |
| `Callable[[int], str]` | 接受 int、返回 str 的函数 | 描述回调 |

函数参数写 `Sequence[str]` 而不是 `list[str]`，调用方传元组也不会被类型检查器拦——**约束放宽，误报减少**。

## 6. `from __future__ import annotations`

放在文件**最顶部**（必须在其他 import 之前）：

```python
from __future__ import annotations
```

作用是让**所有标注变成惰性字符串**，运行时完全不求值。

三个实际好处：

| 好处 | 说明 |
|---|---|
| 前向引用 | 可以在类定义里用尚未定义的类名 |
| 零运行时开销 | 标注不求值，也就不会创建对象 |
| 避免循环导入 | 标注里的名字不会在导入时被解析 |

有了它，`def fit_pca(...) -> PCAResult:` 里的 `PCAResult` 即使写在后面也没问题。

**注意它会影响运行时反射**：用了这个 import 之后，`SomeClass.__annotations__` 里的值是**字符串**而不是类型对象。如果某段代码依赖 `__annotations__` 拿到真实类型（比如某些依赖注入框架），要留意这点。

## 7. 类型别名 vs 变量

```python
Transform = Literal["none", "log1p", "center", "zscore"]   # 类型别名
DEFAULT_TRANSFORM = "center"                                # 变量
```

两者长得一样，区别在**右边的值**：如果右边是个类型，左边就是类型别名；如果右边是普通值，左边就是变量。

约定上，类型别名用大驼峰（`Transform`、`MatrixLike`），常量用全大写（`DEFAULT_TRANSFORM`），靠命名习惯区分。

---

# 二，函数定义的参数形式

## 1. 三种分隔符

Python 的参数列表里可以出现三种"分隔符"，作用完全不同：

```python
def f(a, b, /, c, d, *, e, f, **kwargs):
    ...
```

| 符号 | 位置 | 含义 |
|---|---|---|
| `/` | 参数列表**开头部分** | 它**之前**的参数只能用位置传递 |
| `*` | 中间 | 它**之后**的参数只能用关键字传递 |
| `**kwargs` | 末尾 | 收集任意多余的关键字参数 |

对照调用：

```python
f(1, 2, 3, 4, e=5, f=6)        # ✅
f(1, 2, c=3, d=4, e=5, f=6)    # ✅
f(a=1, b=2, ...)               # ❌ a、b 只能用位置传
f(1, 2, 3, 4, 5, 6)            # ❌ e、f 只能用关键字传
```

`*args` 和 `*` 的区别也要分清：

```python
def g(*args): ...      # 收集任意多个位置参数到元组 args
def h(*, x): ...       # 不收集，只是告诉 Python“x 必须是关键字”
```

## 2. 为什么科学计算接口爱用 `*`

```python
def fit_pca(matrix, *, n_components=None, transform="center"): ...
```

调用时：

```python
fit_pca(m, 2)                  # ❌ TypeError
fit_pca(m, n_components=2)     # ✅
```

**理由是可读性**。参数一多，位置传参就看不懂了：`fit_pca(m, 2, "zscore")` 里的 `2` 是什么？没人知道。强制写关键字，调用处就自带说明。

**另一个实际好处是防错位**。以后如果参数顺序调整，按关键字调用的代码完全不受影响；按位置调用的代码会静默传错参数——而 Python 不会报错，只会算出一个错误结果。

C++ 没有对应语法，只能靠命名约定（如 builder 模式）或 `NamedParameter` 技巧绕。

## 3. 参数顺序的硬规则

合法顺序只有一个方向：

```
位置参数  →  /  →  位置或关键字  →  *args  →  *  →  关键字专用  →  **kwargs
```

两条必须记住的限制：

**限制一：有默认值的必须排在没有默认值的后面。**

```python
def bad(a=1, b): ...      # ❌ SyntaxError
def good(b, a=1): ...     # ✅
```

原因和 C++ 一样：否则 `f(5)` 无法判断 5 该给谁。

**限制二：关键字专用参数不受上一条限制的影响。**

```python
def f(a, *, b=1, c): ...  # ✅ 合法！
```

因为 `c` 只能按关键字传，不存在歧义。

## 4. 默认值的陷阱

**可变对象不能当默认值**：

```python
def bad(items=[]):        # ❌ 默认值只在定义时创建一次！
    items.append(1)
    return items

bad()    # [1]
bad()    # [1, 1]  ← 不是预期的 [1]！
```

原因：`[]` 在函数**定义时**创建并绑定给默认参数，所有"没传参"的调用**共享同一个列表**。

正确写法：

```python
def good(items=None):
    if items is None:
        items = []
    items.append(1)
    return items
```

dataclass 里有专门的解法，见第三节。

## 5. 括号内可以自由换行

括号（`()`、`[]`、`{}`）没闭合时，Python 允许任意换行和缩进，不受缩进块规则约束：

```python
def fit_pca(
    matrix: np.ndarray,
    *,
    n_components: int | None = None,
) -> PCAResult:
```

参数多时一行一个最易读，`git diff` 也清楚。

## 6. 返回类型标注

```python
) -> PCAResult:
```

`->` 后面写返回值类型。对使用者来说这是**接口承诺**：拿到什么类型，不用翻函数体。

配合 dataclass 特别有用——类型名指向一个结构，而这个结构的字段定义就是返回值清单。

---

# 三，dataclass

## 1. 它到底生成了什么

看这段代码：

```python
from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class A:
    x: int

a = A()      # TypeError: A.__init__() missing 1 required positional argument: 'x'
```

**报错本身就回答了问题**：是 `TypeError` 而不是 `AttributeError`，说明 `__init__` **被生成了**，而且要求一个叫 `x` 的参数。

如果 dataclass 没生成 `__init__`，`A()` 会成功创建对象，报错要等到 `a.x` 那一行才出现。报错位置的差异，就是证据。

实测把同一段代码"加 dataclass"和"不加 dataclass"的类字典对比，差异是：

| 方法 | 作用 | 触发条件 |
|---|---|---|
| `__init__` | 构造函数，参数按字段声明顺序 | 总是 |
| `__repr__` | 打印成 `A(x=1)` | 总是 |
| `__eq__` | 按字段逐个比较 | 总是 |
| `__hash__` | 让实例可放进 set / 作 dict 键 | `frozen=True` |
| `__setattr__` / `__delattr__` | 换成会报错的版本 | `frozen=True` |
| `__slots__` | 取消每实例字典 | `slots=True` |
| `__dataclass_fields__` | 字段元数据，供 `fields()` 读取 | 总是 |
| `__dataclass_params__` | 装饰器参数记录 | 总是 |
| `__match_args__` | 支持 `match` 语句按位置匹配 | 总是 |
| `__replace__` | 支持 `copy.replace()`（3.13+） | 总是 |
| `__getstate__` / `__setstate__` | 序列化支持 | 总是 |

也就是说，6 行代码换来了十来行样板。

## 2. 机制：裸标注为什么能当字段用

```python
class Plain:
    x: int          # 只是往 Plain.__annotations__ 记一笔

Plain().x           # AttributeError —— 属性压根没被创建
Plain.__annotations__   # {'x': <class 'int'>}
```

普通类里的"名字 : 类型"是**死的**，只产生一条 `__annotations__` 记录。

`@dataclass` 做的事就一句话：

> **去读 `__annotations__` 这本账，把每条记录当成"这是一个字段"，据此生成 `__init__`。**

$$\text{类体中的裸标注} \xrightarrow{\ \texttt{@dataclass 读取}\ } \text{字段声明} \xrightarrow{\ \text{生成}\ } \texttt{\_\_init\_\_}$$

所以那句"只有类型、没有值"不是省略写法，而是一份**声明**。装饰器是执行者，标注是说明书。

## 3. 字段与默认值

```python
@dataclass
class WithDefault:
    x: int
    y: str = "hello"

WithDefault(1)              # WithDefault(x=1, y='hello')
WithDefault(1, "world")     # WithDefault(x=1, y='world')
```

**字段顺序 = 构造函数参数顺序**，所以规则和函数参数完全一样：有默认值的必须排在后面。

可变默认值要用 `field()`：

```python
from dataclasses import dataclass, field

@dataclass
class Good:
    tags: list[str] = field(default_factory=list)     # ✅ 每次新建一个空列表
```

```python
@dataclass
class Bad:
    tags: list[str] = []                              # ❌ ValueError
```

dataclass **会主动拦住**这个错误并抛出 `ValueError: mutable default <class 'list'> for field tags is not allowed`。这比普通函数的行为好——普通函数只是静默出错。

`field()` 还能做别的：

```python
from dataclasses import field

name: str = field(default="", repr=False)      # 不出现在 __repr__
value: int = field(default=0, compare=False)   # 不参与 __eq__
hidden: str = field(default="", init=False)    # 不出现在 __init__ 参数里
```

## 4. `frozen=True`

```python
a = A(1)
a.x = 99        # FrozenInstanceError: cannot assign to field 'x'
```

拦住的是"**重新绑定字段**"。

附带效果：因为不可变，dataclass 会生成 `__hash__`，实例可以放进 `set` 或当 dict 的键。

**注意它拦不住数组、列表内部内容的修改**：

```python
result.scores[0] = 999      # 语法合法，不报错
```

因为这是"修改字段指向的对象"，不是"重新绑定字段名"。详见第五节。

## 5. `slots=True`

```python
a.__dict__      # 不存在
a.z = 99        # 报错
A.__slots__     # ('x',)
```

普通对象把属性存在每实例的 `__dict__` 里，slots 改成固定槽位：

| 方面 | 普通对象 | slots 对象 |
|---|---|---|
| 存储 | 每实例一个字典 | 固定槽位 |
| 内存 | 较大 | 较小 |
| 访问速度 | 一般 | 略快 |
| 动态加字段 | 可以 | 不允许 |

代价就是最后一条：扩展性没了。但对字段固定的结果结构来说，这正是想要的。

## 6. 一个实现细节：slots 会重建类

实测这个报错：

```text
TypeError: super(type, obj): obj (instance of A) is not an instance or subtype of type (A).
```

报错里**两个都叫 `A` 的类其实不是同一个对象**。原因是 `slots=True` 时 dataclasses 会**创建一个新的类**（因为 `__slots__` 必须在类创建时就声明）。

对照实验：`frozen=True` 但不加 `slots` 的写法，加新字段报的是清楚得多的：

```text
FrozenInstanceError: cannot assign to field 'z'
```

**结论**：不带 slots 时报错统一而清楚，带 slots 时才退化成奇怪的 `TypeError`。这不是语文题，是"重建类"这个实现选择带来的副作用。

## 7. 和 C++ struct 的对照

| Python | C++ |
|---|---|
| `@dataclass` 类 | `struct`（公开成员的纯数据聚合） |
| 字段声明 `x: int` | `int x;` |
| `frozen=True` | 成员为 `const` |
| `slots=True` | 成员在编译期固定（C++ 本来就如此） |
| 生成的 `__init__` | 聚合初始化 |

**关键差异一：Python 没有 struct 这个概念。** 它只有 `class`，而 `class` 要同时扮演 struct 和完整 OOP 对象两种角色。默认状态下它比 C++ 的 struct 灵活得多：

```python
class Foo:
    pass

f = Foo()
f.随便什么 = 123     # 合法！随时能加字段
```

**`@dataclass` 的作用，就是把 Python 的 `class` 收窄成 C++ struct 的形态。** 看到 `frozen=True, slots=True` 一起出现，可以直接读成："我要 C++ struct，不要 Python 那种什么都能塞的灵活对象。"

**关键差异二：Python 对象永远是引用。**

```python
first = A(1)
second = first
second is first        # True —— 只是多了一个名字
```

C++ 的 `A a2 = a1;` 是**真的复制**。这个差异很重要，它解释了为什么 `frozen` 在 Python 里格外有价值：C++ 里你拿到 `const A&`，共享天然安全；Python 里默认共享一个可变对象很危险（谁都能改），所以要靠 `frozen` 把"共享才安全"这件事建立起来。

## 8. 一个会踩的坑：`__eq__` 碰上 NumPy 数组

因为 `__eq__` 是按字段元组比较，而元组比较到 numpy 数组时会调用 `==`，得到的是**逐元素布尔数组**而不是单个布尔值：

```python
fit_pca(m) == fit_pca(m)
# ValueError: The truth value of an array with more than one element is ambiguous
```

这也是为什么 PCA 的测试里从不直接比较 `PCAResult` 对象，而是比较字段：

```python
assert result.scores.shape == (4, 2)
assert np.allclose(result.scores, expected)
```

要支持直接比较，得自己写 `__eq__`（用 `np.array_equal`）并配合 `@dataclass(eq=False)`。

---

# 四，不可变对象

## 1. 为什么结果对象要冻结

`PCAResult` 是算法算完交回来的**结果凭证**，不是工作对象。冻结它等于把"结果不该被改"这条约定写进类型里，而不是靠注释提醒。

如果允许就地改，"我拿到的这个结果到底是什么"就没有保证了——尤其在多层调用、异步、缓存这些场景下。

## 2. 要"改"一个字段怎么办

**因为 `frozen=True`，根本没有"就地修改"这条路。**

### 错误写法一：直接赋值

```python
result.n_features = 999
# FrozenInstanceError: cannot assign to field 'n_features'
```

### 错误写法二：只传想改的那个字段

```python
PCAResult(scores=arr)
# TypeError: PCAResult.__init__() missing 9 required positional arguments: ...
```

`__init__` 把所有字段都当必填参数。这不是"改一个字段"，是"想新建但材料没给够"。

### 正确写法一：全部字段重写

啰嗦，字段一多就抄错，不推荐。

### 正确写法二：`replace()`

```python
from dataclasses import replace

patched = replace(real, n_features=999)
```

**只覆盖点名的字段，其余自动复制**，返回一个**新对象**，原对象完好无损。

实测：

```
原对象：        Pair(scores=array([1., 2.]), n=2, label='first')
replace 后：    Pair(scores=array([1., 2.]), n=3, label='first')
原对象没被动：  Pair(scores=array([1., 2.]), n=2, label='first')
是新对象吗：    True
```

这个模式叫 **copy-on-write**（写时复制）。C++ 类比：成员全是 `const` 的结构体，想改只能造一个新的，`replace` 就是那个"造新结构体"的动作，只是帮你把没改的字段搬过去。

## 3. `replace` 是浅复制

实测：

```python
patched.scores is real.scores      # True
```

新旧两个对象**共享同一个数组对象**。所以：

```python
patched.scores[0] = 999      # real.scores 也跟着变
```

要彻底隔离得手动拷贝：

```python
replace(real, scores=real.scores.copy(), n_features=999)
```

`replace` 的语义是"换掉字段绑定的对象"，不是"复制被绑定的内容"。

实际使用中通常不用手动深拷贝，因为算法返回时已经 `copy` 过一次了。

## 4. 两层保护的分工

这是理解 PCA 返回值设计的关键：

| 手段 | 拦住什么 | 拦不住什么 |
|---|---|---|
| `frozen=True` | 有人把 `result.scores` 换成别的数组 | 有人改 `result.scores` 里的数字 |
| `np.array(x, copy=True)` | 有人改了数组数字（改的是**副本**，不影响内部状态） | — |

两层叠起来，结果才算真正封存。**少任何一层都有漏。**

举例说明为什么 `copy=True` 是必要的：

```python
loadings = vt[:component_count].T     # 切片 + 转置，两个都是视图
```

视图和 `vt` **共享同一块内存**。如果不 `copy` 就交出去，调用方改 `loadings` 会同时改掉内部 `vt`，反之亦然。

## 5. 回到 Python 引用语义

```python
second = first          # 只是多一个名字，不复制
second is first         # True
```

**Python 里没有 C++ 那种"值语义"**：赋值、传参、放进列表，传的都是引用。

| 场景 | C++ | Python |
|---|---|---|
| `B b = a;` | 复制一份 | 多一个名字 |
| 函数传参（非引用） | 复制 | 传引用 |
| 修改参数 | 不影响原对象 | 影响原对象 |

所以 Python 里想"安全地共享"，手段不是复制，而是**不可变**——这也是 `frozen=True` 在工程代码里出现频率很高的原因。

---

# 五，其他常见现代写法

## 1. f-string

```python
name = "PCA"
f"算法：{name}"                    # 算法：PCA
f"{version:.2f}"                   # 保留两位小数
f"{value:>10}"                     # 右对齐宽度 10
f"{result=}"                       # 调试用：result=<值>
```

3.6 引入，目前最推荐的字符串格式化方式（比 `%` 和 `.format()` 都快且清楚）。

## 2. 推导式

```python
squares = [x * x for x in range(5)]              # 列表
evens = [x for x in data if x % 2 == 0]          # 带条件
lookup = {k: v for k, v in pairs}                # 字典
unique = {x for x in data}                       # 集合
total = sum(x * x for x in data)                 # 生成器（不建列表，省内存）
```

最后一行值得注意：用 `()` 而不是 `[]` 时创建的是**生成器**，它不把全部元素放进内存，而是边算边给。数据量大时这个差别很重要。

## 3. 解包

```python
a, b = 1, 2                    # 基本解包
u, s, vt = np.linalg.svd(m)    # 接住函数返回的三元组
first, *rest = [1, 2, 3]       # 星号收集剩余 → first=1, rest=[2,3]
f(*args)                       # 把列表拆成位置参数
g(**kwargs)                    # 把字典拆成关键字参数
```

`u, s, vt = np.linalg.svd(...)` 这种"元组解包"在读线性代数代码时非常常见。

## 4. 链式比较

```python
if 1 <= n <= available:        # Python 独有
```

等价于 `1 <= n and n <= available`，但 `n` 只求值一次。C 系语言里必须写成两个比较加 `and`。

## 5. try / except / else / finally

```python
try:
    risky()
except ValueError as error:
    handle(error)
except (TypeError, KeyError):      # 一个 except 捕获多种
    ...
else:
    ...                            # 没有异常时执行
finally:
    cleanup()                      # 无论成败都执行
```

两个容易忽略的点：

- `as error` 拿到异常对象，可以打印、可以 `.args` 取值；
- 异常链用 `raise ... from error` 保留原始起因，报错信息里会显示"Caused by"。

```python
try:
    values[i, j] = float(cell)
except ValueError as error:
    raise ValueError(f"第 {i} 行第 {j} 列不是合法数值：{cell!r}") from error
```

这样既不吞掉原始错误，又给了更有用的上下文。

## 6. with 语句与上下文管理器

```python
with path.open("r", encoding="utf-8") as handle:
    ...
```

离开 `with` 块时自动关闭文件，即使中间抛异常也保证执行。等价于手写 `try/finally`，但短得多。

## 7. 常见的内置函数

| 函数 | 作用 |
|---|---|
| `zip(a, b)` | 并排迭代两个序列 |
| `enumerate(seq)` | 同时拿到下标和值 |
| `isinstance(x, (int, float))` | 同时检查多个类型 |
| `sorted(items, key=lambda x: -x.value)` | 按自定义键排序 |
| `any(...)` / `all(...)` | 内置版，用于可迭代对象 |

`isinstance` 第二个参数传元组可以同时匹配多个类型，这是很常用的写法。

**一个 numpy 相关的坑**：`np.int64(2)` **不是** `int` 的实例：

```python
isinstance(np.int64(2), int)        # False
isinstance(np.int64(2), np.integer) # True
```

所以在要接受 numpy 整数的接口里，必须写成 `isinstance(x, (int, np.integer))`。

---

# 六，和竞赛写法的对照速查

| 场景 | 传统写法 | 现代写法 |
|---|---|---|
| 纯数据类 | 手写 `__init__` 和全部赋值 | `@dataclass` |
| 不可变结构 | 无（靠自觉） | `@dataclass(frozen=True)` |
| 固定布局 | 无 | `slots=True` |
| 改一个字段 | 直接赋值 | `replace(obj, 字段=值)` |
| 限定取值 | 靠注释 | `Literal[...]` |
| 可选参数 | `x=None` + 文档 | `int \| None` |
| 强制关键字 | 命名约定 | `*,` |
| 字符串拼接 | `%` 或 `+` | f-string |
| 建列表 | 循环 + append | 推导式 |

返回[[Python-分站点]]

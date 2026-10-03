**NumPy：从数组结构到 PCA 实战**

本文把 NumPy 的基础机制和 Project IDEA 生物方法模块里的实际用法放在一起讲。每个结论尽量配实测输出，便于对照自己的运行结果。

---

# 零，写在前面

在这一节之前，我们需要区分一个很容易混淆的问题：几何维度与数据维度

几何维度指的是向量描述的空间是几维的，向量中有几个数，维度就是几

数据维度指的是存储向量数据的数表是几维的，数表有几类数据，维度就是几

区别：例如数组的数据维度是一，但数组描述的几何维度不一定是一，矩阵的数据维度是二，但三阶方阵的几何维度是三

---

# 一，ndarray 的基本结构

## 1. 它是什么

`np.ndarray` 是 NumPy 的核心对象：**一块连续内存 + 一份形状说明**。

这个描述不是比喻，是字面事实。理解这一点，后面所有"为什么不需要遍历""为什么切片不复制数据""为什么能广播"都能推出来。

## 2. 四个核心属性

```python
data = np.zeros((5, 3))
```

| 属性 | 值 | 含义 |
|---|---|---|
| `data.shape` | `(5, 3)` | 各维长度的元组 |
| `data.ndim` | `2` | **维度数**，不是形状 |
| `data.size` | `15` | 元素总数 |
| `data.dtype` | `float64` | 元素类型 |

实测输出：

```
shape = (5, 3) | ndim = 2 | len = 5 | size = 15
```

**`ndim` 和 `shape` 最容易混**：

- `ndim = 2` → "这是个二维的东西"
- `shape = (5, 3)` → "每一维分别有多长"

代码里这两个检查是两件事：

```python
if data.ndim != 2:                            # 是不是二维？
if data.shape[0] < 2 or data.shape[1] < 1:    # 每一维够不够长？
```

## 3. `len()` 和 `shape[0]` 的区别

```python
len(data)        # 5    —— 只有第 0 维长度
data.shape[0]    # 5    —— 同上
data.shape[1]    # 3    —— 列数，len() 拿不到
```

`len()` 只能告诉你"有多少行"，**无法表达列数**。所以矩阵代码里一律用 `shape`，不用 `len`。

## 4. 为什么必须是矩形

这是 NumPy 数组和 Python 嵌套列表最根本的区别：

```python
ragged = [[1.0, 2.0, 3.0], [4.0, 5.0]]    # 纯 Python 合法，每行可以不同长

np.array(ragged)
# ValueError: setting an array element with a sequence.
#             The requested array has an inhomogeneous shape after 1 dimensions.
#             The detected shape was (2,) + inhomogeneous part.
```

翻译成人话："第 0 维有 2 个元素，然后形状就不规则了。"

**构造时就直接拒绝。** 因此只要一个对象是合法的 `ndarray`，每行每列长度一定一致。

这条推论很实用：`np.asarray(x, dtype=np.float64)` 一旦成功返回，就等于**隐含保证了矩形性**，后面不用再检查"各行长度是否一致"。

对照三种语言模型：

| 模型 | 能否参差不齐 | 怎么知道尺寸 |
|---|---|---|
| C 的 `int arr[3][4]` | 不可以 | 编译期就知道 |
| C 的 `int **arr` | 可以 | 必须额外传 m、n，或遍历找哨兵 |
| Python 嵌套列表 | **可以** | **必须遍历** |
| NumPy ndarray | 不可以，构造时拒绝 | 读 `shape`，$O(1)$ |

**所以查行列数永远不需要遍历。** shape 是"数组的说明书"，不是"从数据里总结出来的结论"。遍历除了把 $O(1)$ 变成 $O(n \times p)$，结论一定和 `shape` 一样。

## 5. 内存布局与 strides

既然底层是连续内存，"二维"是怎么表达的？靠 `strides`：

```python
a = np.zeros((5, 3))          # float64，每个元素 8 字节
a.strides                     # (24, 8)
```

读法是：**走到下一行要跨 24 字节，走到下一列要跨 8 字节**。因为一行有 3 个元素，$3 \times 8 = 24$。

有了 strides，"二维数组"就只是"一维内存 + 跳转规则"。这也解释了：

- 为什么 `shape` 是元数据（跳转规则而已）；
- 为什么切片能是**视图**（改一下起始偏移和 strides 就行，不用搬数据）；
- 为什么 NumPy 快（运算直接按 strides 做内存跳转，C 层循环）。

## 6. 创建数组

```python
np.array([1, 2, 3])              # 从 Python 列表创建
np.zeros((5, 3))                 # 全 0
np.ones((5, 3))                  # 全 1
np.empty((5, 3))                 # 不初始化（快，但内容是垃圾值）
np.arange(10)                    # 0..9
np.linspace(0, 1, 5)             # 0 到 1 等分 5 个点
np.full((5, 3), 7.0)             # 全部填 7
```

**`np.zeros` 和 `np.empty` 的区别**：`zeros` 会清零（有开销），`empty` 不初始化，所以快——但内容是内存里的残留值。只有在**紧接着就要覆盖全部元素**时才用 `empty`。

---

# 二，索引与切片

## 1. 基础切片

```python
data[0]          # 第 0 行，形状 (3,)
data[:, 0]       # 第 0 列，形状 (5,)
data[1:3]        # 第 1、2 行，形状 (2, 3)
data[:, :2]      # 前两列，形状 (5, 2)
data[::2]        # 每隔一行
data[::-1]       # 反转行序
```

**逗号前面是行的选择，后面是列的选择。** 用 `:` 表示"全要"。

Python 切片是**左闭右开**：`data[1:3]` 包含第 1、2 行，不含第 3 行。

## 2. 标记维度

单独一个 `:` 在末尾可以省略，也可以用 `...` 代替多个：

```python
data[:, 1]        # 明确
data[..., 1]      # 等价，多维时更简洁
```

`np.newaxis`（或 `None`）用来**插入一个新维度**，这在广播里很有用：

```python
a = np.array([1, 2, 3])       # shape (3,)
a[:, None].shape              # (3, 1)  变成列向量
a[None, :].shape              # (1, 3)  变成行向量
```

## 3. 布尔索引

```python
mask = data > 0
data[mask]                    # 取出所有大于 0 的元素，返回一维数组
data[data > 0]                # 常写成一行
```

用途：筛选、条件赋值。

```python
data[data < 0] = 0            # 把所有负数截断成 0
```

**注意布尔索引返回的是复制**，形状是"被选中的元素个数"，不是原来的形状。

## 4. 花式索引

用一个整数数组当索引：

```python
data[[0, 2, 4]]               # 取第 0、2、4 行
data[:, [1, 0]]               # 列顺序换成 1、0（相当于交换两列）
```

用途：重排、按特定顺序抽取。

排序场景里很常见：

```python
order = np.argsort(-np.abs(column), kind="stable")   # 按绝对值降序的索引
column[order]                                        # 按该顺序重排
```

`np.argsort` 返回的是"**排序后的下标**"而不是排序后的值。这样原数组不用动，用一个索引数组就能表达任意顺序。PCA 里的载荷排序就是这么做的。

## 5. 切片 vs 花式索引：视图与复制的分界

| 操作 | 返回 |
|---|---|
| 基础切片 `a[1:3]` | **视图**（共享内存） |
| 布尔索引 `a[a > 0]` | 复制 |
| 花式索引 `a[[0, 2]]` | 复制 |
| 转置 `a.T` | **视图** |
| 算术运算 `a * 2` | 复制 |

**规律**：能不能用"改起始位置 + 改 strides"表达？

- 能 → 视图（切片、转置、reshape）
- 不能 → 只能造新数组（布尔索引、花式索引、算术）

---

# 三，axis

## 1. 怎么读

`axis=k` 的意思是"**沿着第 k 维压缩**"。最可靠的记法是**看结果的形状**：

```python
data.shape          # (5, 3)

data.mean(axis=0)   # (3,)  ← 第 0 维被压掉，得到“每个特征”的均值
data.mean(axis=1)   # (5,)  ← 第 1 维被压掉，得到“每个样本”的均值
data.mean()         # 标量，全部压掉
```

PCA 里要的是**每个特征的均值**（中心化时要减它），所以用 `axis=0`。

## 2. 一句话记法

> **`axis` 是"要消失的那一维"。**

`axis=0` 消失的是行 → 结果按列组织 → "每个特征的统计量"。

## 3. 常见函数都接受 axis

```python
data.mean(axis=0)      # 均值
data.std(axis=0)       # 标准差
data.sum(axis=0)       # 求和
data.max(axis=0)       # 最大值
data.argmax(axis=0)    # 最大值的位置
np.cumsum(data, axis=0)# 累计求和
```

## 4. `keepdims`：保留被压掉的那一维

```python
data.mean(axis=0).shape               # (3,)
data.mean(axis=0, keepdims=True).shape # (1, 3)
```

保留成 `(1, 3)` 的好处是**可以直接参与广播**：

```python
centered = data - data.mean(axis=0, keepdims=True)   # 形状严格对应
```

不保留也能广播成功（`(5,3) - (3,)` 合法），但显式对齐形状能让人一眼看懂维度是怎么配上的。

---

# 四，广播（Broadcasting）

## 1. 规则

NumPy 允许形状不同的数组做运算，靠的是广播。规则是**从最右边开始逐维比较**：

$$ \text{两维相等} \quad \text{或} \quad \text{其中一维为 } 1 $$

满足就把长度为 1 的那一维**拉伸**去匹配，不满足就报错。

更多细节：如果其中一个数组维度更少，就在**左边补 1**，再逐维比。

## 2. 例子一：中心化

```python
data.shape          # (n, p)    n 个样本，p 个特征
feature_mean.shape  # (p,)      每个特征的均值

centered = data - feature_mean
```

对齐过程：

```
   (n, p)
-     (p,)        ← 左边补 1 变成 (1, p)
─────────────
   (n, p)        ← 每一行都减去同一个均值向量
```

**一行代码表达了 $Z_{ij} = X_{ij} - \mu_j$** —— 每个样本减去它自己的特征均值，不用写循环。

## 3. 例子二：还原样本得分

```python
u.shape                # (n, k)
singular_values.shape  # (k,)

scores = u[:, :k] * singular_values
```

```
   (n, k)
×     (k,)        ← 当作 (1, k)
─────────────
   (n, k)        ← 每一列乘以对应的奇异值
```

**结果就是 $T = U S$。**

## 4. 为什么广播不只是"省字"

广播把"对每一行/列做同一个操作"从**显式循环**变成了**一个表达式**：

```python
# 广播写法
centered = data - data.mean(axis=0)

# 等价的显式循环（慢得多，也长得多）
centered = np.empty_like(data)
for i in range(data.shape[0]):
    for j in range(data.shape[1]):
        centered[i, j] = data[i, j] - data[:, j].mean()
```

关键差别不只是行数：**广播让运算在 NumPy 的 C 层循环里完成**，而 Python 的 `for` 循环每一轮都要做类型检查、对象创建、边界检查。数据量大时差距是几十倍。

## 5. 坑

**坑一：从右往左对齐，不是从左往右。**

```python
np.zeros((5, 3)) + np.zeros((5,))     # ❌ 报错
```

对齐过程：`(5, 3)` 和 `(5,)` → 右边比，`3 vs 5` 冲突 → 报错。

```python
np.zeros((5, 3)) + np.zeros((3,))     # ✅ 右边 3 vs 3 匹配
np.zeros((5, 3)) + np.zeros((5, 1))   # ✅ (5,1) 拉伸成 (5,3)
```

**坑二：报错信息长这样**

```
ValueError: operands could not be broadcast together with shapes (5,3) (5,)
```

看到这个，先把两个形状右对齐写下来比较，冲突的那一维就是问题所在。

**坑三：广播不复制，但结果一定新建。**

广播只是在计算时按规则取元素，不产生中间数组；但运算结果本身是新数组，占内存。

---

# 五，视图与复制

这是 NumPy 最容易静默出错的地方。

## 1. `np.array` 与 `np.asarray`

| 函数 | 行为 |
|---|---|
| `np.array(x)` | **总是**复制一份 |
| `np.asarray(x)` | 已经是同 dtype 的 `ndarray` 就**直接用**，不复制 |

PCA 代码用 `asarray` 是为了省一次没必要的复制：

```python
data = np.asarray(matrix, dtype=np.float64)
```

如果传进来的本来就是 `float64` 数组，这一步零开销。

## 2. 哪些操作返回视图

| 操作 | 是否视图 |
|---|---|
| 基础切片 `a[1:3]`、`a[:, :5]` | **视图** |
| 转置 `a.T` | **视图** |
| `reshape` | 通常视图 |
| `a.ravel()` | 通常视图 |
| 布尔索引 `a[a > 0]` | 复制 |
| 花式索引 `a[[0, 2]]` | 复制 |
| 算术运算 `a * 2`、`a + b` | 复制 |
| `a.copy()` | 复制 |

## 3. 共享内存的后果

```python
loadings = vt[:component_count].T     # 切片 + 转置，两个都是视图

loadings[0, 0] = 999
# vt[0, 0] 也变成了 999 —— 因为它们是同一块内存
```

这是函数返回时的隐患：调用方拿到一个"看起来是结果"的东西，改动它却影响了函数内部的状态。

## 4. 解决办法：`copy=True`

```python
return PCAResult(
    scores=np.array(scores, copy=True),
    loadings=np.array(loadings, copy=True),
    ...
)
```

`np.array(x, copy=True)` **强制复制**，把这块记忆切断，交出去的东西从此独立。

对 PCA 来说，`scores` 因为做了乘法（`u[:, :k] * s`）本来就是新数组，而 `loadings` 来自 `vt[:k].T`，**确实是视图**——所以这里的 `copy` 不是形式主义，是必要的。

## 5. 和 `frozen` 的关系

| 手段 | 拦住什么 | 拦不住什么 |
|---|---|---|
| `copy=True` | 视图共享导致的内外串改 | — |
| `@dataclass(frozen=True)` | 有人把字段整个换掉 | 有人改数组里的数字 |

`frozen=True` **拦不住**数组内容的修改：

```python
result.scores[0] = 999        # 语法合法，不报错
```

两者用途不同、缺一不可。

## 6. 判断口诀

> **看到切片、转置、reshape，就假设它是视图；要交出去之前，先 `copy`。**

要确认的话可以验身份：

```python
a = np.zeros((3, 3))
b = a[:2]
b.base is a          # True  → b 是 a 的视图
```

`.base` 指向"我依赖的那个数组"，是视图时会指向原数组，是独立数组时是 `None`。

---

# 六，常用函数与数值细节

## 1. `np.log1p(x)`：算 $\log(1+x)$

```python
np.log1p(x)
```

**为什么不写 `np.log(1 + x)`？**

精度。当 $x$ 极小时（如 $10^{-20}$），浮点里 `1 + x` 会**丢掉** $x$ 的有效数字，算出 `log(1.0) = 0`，而真值是 $\approx 10^{-20}$。

`log1p` 用专门算法绕过这个损失。NumPy 里同类的还有 `expm1`、`hypot` 等，都是为数值稳定性准备的。

用途：对右偏的计数/丰度数据做变换，$Z_{ij} = \log(1 + X_{ij})$。**它不替代中心化**——做完还要中心化。

## 2. 检查与逻辑

| 函数 | 作用 | 返回 |
|---|---|---|
| `np.isfinite(a)` | 逐元素判断是否有限（非 NaN、非 inf） | 同形状布尔数组 |
| `np.any(a)` | 只要有一个 `True` 就 `True` | 标量 |
| `np.all(a)` | 全部为 `True` 才 `True` | 标量 |

组合用法：

```python
if not np.isfinite(data).all():
    raise ValueError("不能包含 NaN 或无穷值")

if np.any(data < 0):
    raise ValueError("要求所有输入值都不小于 0")
```

**为什么必须拦 NaN**：NaN 会污染整个 SVD，输出全是 NaN 但**程序不报错**。门口拦住比事后排查省事得多。

## 3. 统计量：`mean` / `std` 与 `ddof`

```python
data.mean(axis=0)                  # 每个特征的均值，形状 (p,)
centered.std(axis=0, ddof=0)       # 每个特征的标准差，形状 (p,)
```

`ddof` = delta degrees of freedom，自由度修正：

| 取值 | 除以 | 名称 | 典型使用者 |
|---|---|---|---|
| `ddof=0` | $n$ | 总体标准差 | scikit-learn `StandardScaler` |
| `ddof=1` | $n-1$ | 样本标准差 | R `prcomp(scale=TRUE)`、`pandas.std` |

**两者结果不同，跨工具比对时必须确认口径。**

PCA 代码里的组合是：z-score 用 `ddof=0`，特征值用 $n-1$。这与 sklearn 一致，但与 R 不同。后果是 z-score 后每个特征的样本方差（$n-1$ 口径）等于 $n/(n-1)$ 而不是精确的 1。

## 4. `np.cumsum`：累计求和

```python
np.cumsum([0.5, 0.3, 0.15, 0.05])
# array([0.5, 0.8, 0.95, 1.0])
```

用于把"每个主成分的解释方差比例"变成"前 k 个的累计比例"。

对应 $\operatorname{cumulative}_k = \sum_{l=1}^{k}\operatorname{explained\_ratio}_l$。

## 5. `np.argsort`：排序下标

```python
order = np.argsort(-np.abs(column), kind="stable")
```

- `np.abs(column)` → 取绝对值（载荷符号可整体翻转，排序要看绝对值）
- 前面加负号 → 把升序变成**降序**
- `kind="stable"` → 保持相等元素的原有相对顺序

返回的是**下标数组**，用它去索引原数组就得到排序结果：

```python
ranked = column[order]
```

`argsort` 而不是 `sort` 的好处：**原数组不动，排序顺序可以复用到别的数组上**。

## 6. `np.linalg.svd` 与 `full_matrices`

```python
u, singular_values, vt = np.linalg.svd(centered, full_matrices=False)
```

| `full_matrices` | `u` 形状 | `vt` 形状 |
|---|---|---|
| `True`（默认） | $(n, n)$ | $(p, p)$ |
| `False` | $(n, k)$ | $(k, p)$ |

其中 $k = \min(n, p)$。

**默认值在 PCA 里是灾难**：$p = 20000$ 时会构造一个 $20000 \times 20000$ 的 `vt`，约 32 亿个浮点数，全是无用数据。所以**必须设 `False`**——这不是优化，是可行性的前提。

返回值含义：

$$ Z = U S V^{\mathsf{T}} $$

| 返回值 | 对应 | 说明 |
|---|---|---|
| `u` | $U$ | 列为样本空间方向，$(n, k)$ |
| `singular_values` | $S$ 的对角元 | 一维数组，$(k,)$ |
| `vt` | $V^{\mathsf{T}}$ | $(k, p)$，所以**每一行**是一个主方向 |

注意 `vt` 是 $V$ 的**转置**（名字就是 v-transpose）。要拿载荷矩阵 $V$，需要 `vt[:k].T`。

## 7. 其他会在生物模块用到的

| 函数 | 作用 |
|---|---|
| `np.array_equal(a, b)` | 判断内容是否完全相同 |
| `np.allclose(a, b, atol, rtol)` | 判断浮点数近似相等（**测试里用得最多**） |
| `np.concatenate` / `np.vstack` / `np.hstack` | 拼接 |
| `np.clip(a, lo, hi)` | 截断到区间内 |
| `np.round(a, n)` | 四舍五入到 n 位 |
| `np.dot` / `@` | 矩阵乘法 |
| `np.linalg.norm` | 向量/矩阵范数 |
| `np.einsum` | 爱因斯坦求和，表达复杂张量运算 |

**测试里为什么用 `allclose` 而不是 `==`**：浮点运算有舍入误差，`0.1 + 0.2 != 0.3`。`allclose` 判断"差是否小于容差"，这才是浮点比较的正确方式。

---

# 七，PCA 里的 NumPy 用法汇总

把 `fit_pca` 里的每个 NumPy 操作和它对应的数学对象列在一起，读代码时可以直接对照。

| 代码 | NumPy 机制 | 数学对象 |
|---|---|---|
| `np.asarray(matrix, dtype=np.float64)` | 转换 + 保证矩形 | $X$，实数矩阵 |
| `data.ndim != 2` | 属性读取 | 检查是矩阵 |
| `data.shape` | 元数据，$O(1)$ | $(n, p)$ |
| `np.isfinite(data).all()` | 逐元素检查 + 归约 | 拒绝 NaN/inf |
| `data.mean(axis=0)` | 沿第 0 维压缩 | $\mu_j$，特征均值 |
| `data - feature_mean` | 广播 | $Z_{ij} = X_{ij}-\mu_j$ |
| `centered.std(axis=0, ddof=0)` | 统计 + 自由度为 0 | $\sigma_j$ |
| `centered / feature_scale` | 广播 | $Z_{ij}=(X_{ij}-\mu_j)/\sigma_j$ |
| `np.linalg.svd(..., full_matrices=False)` | 内存受控的 SVD | $Z = USV^{\mathsf{T}}$ |
| `singular_values.shape[0]` | 属性读取 | $k=\min(n,p)$ |
| `np.sum(singular_values**2)/(n-1)` | 归约 + 广播除法 | $\operatorname{tr}(C)$，总方差 |
| `u[:, :k] * singular_values` | 切片 + 广播 | $T = US$，得分 |
| `vt[:k].T` | 切片 + 转置（视图） | $V$，载荷 |
| `singular_values**2/(n-1)` | 逐元素幂 | $\lambda_k = s_k^2/(n-1)$ |
| `eigenvalues / total_variance` | 逐元素除法 | 解释方差比例 |
| `np.cumsum(explained_ratio)` | 累计求和 | 累计解释方差 |
| `np.array(x, copy=True)` | 强制复制，断开视图 | 封存结果 |

**真正的数学运算只有 `svd` 一行加几行逐元素运算。** 其余都是校验、约定和内存管理——这是工程代码和教科书公式最大的区别。

---

# 八，常见报错对照

| 报错信息 | 原因 | 处理 |
|---|---|---|
| `The requested array has an inhomogeneous shape` | 传入了参差不齐的嵌套列表 | 补齐每行长度，或用 `dtype=object`（但那就不是数值矩阵了） |
| `operands could not be broadcast together with shapes (5,3) (5,)` | 广播对齐失败 | 把形状右对齐比较，把 `(5,)` 改成 `(3,)` 或 `(5,1)` |
| `The truth value of an array with more than one element is ambiguous` | 对数组用了 `if` / `and` / dataclass 的 `==` | 用 `.all()` / `.any()` / `np.array_equal` |
| `IndexError: index 5 is out of bounds for axis 0 with size 5` | 下标越界 | 索引从 0 开始，检查上界 |
| `ValueError: could not broadcast input array` | 赋值时形状不匹配 | 检查两边形状 |
| `TypeError: only integer scalar arrays can be converted to a scalar index` | 拿浮点数或数组当索引 | 用 `int()` 转换，或改用花式索引 |
| `ValueError: cannot reshape array of size 6 into shape (4,)` | 元素总数不匹配 | 元素个数必须守常 |

---

# 九，一句话总结

| 概念 | 一句话 |
|---|---|
| ndarray | 连续内存 + 形状说明（元数据） |
| `shape` | $O(1)$ 读到的说明书，不需要遍历 |
| 矩形性 | 构造时强制，参差不齐进不来 |
| `axis` | "要消失的那一维" |
| 广播 | 从右往左对齐，为 1 的维度被拉伸 |
| 视图 | 切片/转置/reshape 共享内存，交出去前先 `copy` |
| `asarray` vs `array` | 前者能不复制就不复制 |
| `allclose` | 浮点比较的正确方式 |
| `full_matrices=False` | 大矩阵下从"慢"变成"可行" |
| `log1p` | 小数值下比 `log(1+x)` 精确 |

返回[[Python-分站点]]

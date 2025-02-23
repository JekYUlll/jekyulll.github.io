
Linux 内核使用 **结构体** 和 **函数指针** 的组合模拟面向对象（OO）编程范式。

---

### 1. **结构体封装数据与行为**
   - **数据抽象**：将相关属性和状态封装在一个 `struct` 中。
   - **行为绑定**：通过函数指针将操作绑定到结构体上，实现动态调用。

#### 示例：`struct file_operations`
```c
// 定义文件操作的函数指针表
struct file_operations {
    ssize_t (*read)(struct file *, char __user *, size_t, loff_t *);
    ssize_t (*write)(struct file *, const char __user *, size_t, loff_t *);
    // ... 其他方法
};

// 具体文件系统的实现（如 ext4）
static struct file_operations ext4_fops = {
    .read = ext4_read,
    .write = ext4_write,
    // ... 初始化其他方法
};

// 注册到 VFS 层时关联 fops
struct inode *inode = ...;
inode->i_fop = &ext4_fops; // 绑定特定方法集
```

---

### 2. **多态与继承**
   - **父子结构体**：子结构体嵌入父结构体以继承接口。
   - **类型安全转换**：通过 `container_of` 宏从父指针获取子结构体。

#### 示例：`struct kobject` 与自定义对象
```c
// 父结构体（类似抽象基类）
struct kobject {
    const char *name;
    struct kset *kset;
    // 公共方法
    int (*release)(struct kobject *);
};

// 子结构体（具体实现）
struct my_device {
    struct kobject kobj; // 继承 kobject
    int data;
};

// 实现父类的方法
static void my_device_release(struct kobject *kobj) {
    struct my_device *dev = container_of(kobj, struct my_device, kobj);
    // 清理资源
}

// 初始化时绑定方法
struct my_device *dev = kzalloc(sizeof(*dev), GFP_KERNEL);
dev->kobj.release = my_device_release;
kobject_init(&dev->kobj, &my_device_ktype); // 注册类型
```

---

### 3. **组合与接口分离**
   - **模块化设计**：通过组合而非继承复用代码。
   - **统一接口**：顶层结构体定义标准接口，底层实现差异化逻辑。

#### 示例：`struct block_device`
```c
// 通用块设备接口
struct block_device {
    struct gendisk *disk;
    // 通用方法
    int (*ioctl)(struct block_device *, unsigned int, unsigned long);
};

// 桌面硬盘驱动实现
struct my_disk {
    struct block_device bdev;
    // 私有数据
};

// 实现接口方法
static int my_disk_ioctl(struct block_device *bdev, unsigned int cmd, ...) {
    struct my_disk *disk = container_of(bdev, struct my_disk, bdev);
    return custom_ioctl(disk, cmd);
}
```

---

### 4. **运行时多态**
   - 函数指针作为虚函数表（vtable），根据对象类型动态调用不同实现。

#### 示例：`struct net_device_ops`
```c
// 网络设备操作接口
struct net_device_ops {
    int (*ndo_open)(struct net_device *dev);
    int (*ndo_stop)(struct net_device *dev);
};

// 以太网驱动实现
static struct net_device_ops eth_ops = {
    .ndo_open = eth_open,
    .ndo_stop = eth_stop,
};

// 注册网络设备时绑定 ops
struct net_device *netdev = alloc_etherdev(sizeof(struct priv_data));
netdev->netdev_ops = &eth_ops;
```

---

### 5. **关键技巧**
   - **自引用结构体**：通过指针成员隐式关联自身。
   - **宏简化代码**：如 `container_of` 用于反向查找结构体。
   - **模块化加载**：通过 `struct module` 动态注册/卸载驱动。

---

### 总结
Linux 内核通过 **结构体+函数指针** 实现了以下 OO 特性：
- **封装**：隐藏内部细节（如 `struct inode` 的实现）。
- **多态**：同一接口（如 `read()`）适配多种设备。
- **继承**：子结构体复用父结构体的方法。
- **动态绑定**：运行时选择具体函数实现。

这种设计平衡了 C 语言的静态特性与内核对灵活性的需求，成为高效且可扩展的系统核心。

---

[container_of函数详解](https://blog.csdn.net/u011029104/article/details/136190755)。

主要作用就是根据结构体中的已知的成员变量的地址，来寻求该结构体的首地址。

```c
 /**
  * container_of - cast a member of a structure out to the containing structure
  * @ptr:    the pointer to the member.
  * @type:   the type of the container struct this is embedded in.
  * @member: the name of the member within the struct.
  *
  * WARNING: any const qualifier of @ptr is lost.
  */
 #define container_of(ptr, type, member) ({              \
     void *__mptr = (void *)(ptr);                   \
     static_assert(__same_type(*(ptr), ((type *)0)->member) ||   \
               __same_type(*(ptr), void),            \
               "pointer type mismatch in container_of()");   \
     ((type *)(__mptr - offsetof(type, member))); })
```
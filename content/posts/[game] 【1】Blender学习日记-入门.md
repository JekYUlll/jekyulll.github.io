+++
date = '2025-04-25T06:05:47+08:00'
draft = false
title = '【1】Blender学习日记-入门'
author = 'JekYUlll'
lastmod = '2025-04-25T06:05:47+08:00'
tags = ['game']
categories = ['game']
+++

半夜学一下blender。  
<!-- 在Mint上也装了一下。   -->

[【Kurt】Blender零基础入门教程 | Blender中文区新手必刷教程(已完结)](https://www.bilibili.com/video/BV14u41147YH)。第四课著名的珍珠耳环少女，感觉一节课就差不多学会基础操作了，不错。  

`N`是变换，`S`是size（也可以结合方向轴，在轴向伸缩），`R`是旋转（可以输数字，例如`R` `Z` `90`就是在Z轴旋转90度），`G`是移动。  
新建内容是`SHIFT`+`A`，`F9`可以改段数。`SHIFT`+`D`复制，移动的时候可以例如按`Z`锁定在Z轴上移动。按两下方向轴（例如移动的时候），可以从世界坐标系切换到局部坐标系。  
按`/`可以单独显示该物体。  
在窗口左上角拉一下，能拉出新窗口。新窗口的右上角，可以拉回去。  
摄像机模式也可以按`N`，在"视图"里把摄像机"锁定到视图方位"，方便调整。  

blender有两个渲染引擎：*EEVEE*（快、实时）、*CYCLES*（物理写实）。
渲染快捷键F12。  
右键物体，平滑着色。右边物体数据属性（绿色三角）-> 法向 -> 自动光滑。

编辑->偏好设置里能调整CUDA设置。  
发现用CYCLES的时候，切换成GPU渲染不出东西。显示`Error CUDA kernel for this graphics card compute capability(8.6) not found`，可能是因为显卡驱动太新——毕竟我用的 2.83 的远古Blender（以前转mmd格式的时候，插件比较老，所以下的老版本）。

多选之后->`CTRL`+`L`->关联材质。
+++
date = '2024-07-29T21:05:47+08:00'
draft = false
title = 'OpenGL 初学笔记 -- Cherno + LearnOpenGL'
author = 'JekYUlll'
lastmod = '2024-12-22T21:05:47+08:00'
tags = ['opengl', 'graphics', 'game']
categories = ['game']
+++

![hearder](/images/ayanami_header.jpg)

这两个月学习了一下OpenGL。从Cherno的教学视频开始学习，看完后开始看LearnOpenGL，应该是很常见的学习路径。  
在此以新手视角，记录一下学习中在工程方面遇到的一些坑（数学和底层方面就不打算开口丢人了）。

[Cherno主页](https://www.youtube.com/@TheCherno) | [LearnOpenGL](https://learnopengl.com/)

---

## **1.** 直接选择 64 位

Cherno视频是2017及之前的，为了兼容性，教程里32位。而LearnOpenGL写到后面是64位，还要用Assimp库，默认是编译成64位。建议直接x64，像我这样闷头跟着写的话要把 GLEW 和 GLFW 的静态库全换一遍，或者去折腾CMake。

---

## **2.** GLEW, GLAD, GLFW

这三个比较常用。两个教程的选择都是 GLEW + GLFW，其中 GLEW 和 GLAD 定位相似，都是用于访问OpenGL函数。可以先看看自己喜欢哪一个，免得后面想换再费功夫。

---

## **3.** `Texture` 的实现 -- 小心析构函数

LearnOpenGL中的`Texture`只是一个存储数据的结构体：

```cpp
struct Texture {
    GLuint id;
    string type;
    aiString path;
};
```
而Cherno将`Texture`创建为类，构造函数中直接完成加载图片的操作，并且在析构函数里调用`glDeleteTextures`。  
如果无脑缝代码就完蛋了，因为LearnOpenGL在`Model::loadMaterialTextures`函数中创建了`Texture`的临时对象并返回，会调用析构函数：

```cpp
vector<Texture> loadMaterialTextures(aiMaterial* mat, aiTextureType type, string typeName)
{
    vector<Texture> textures;
    // ...
    return textures;
}
```
可以选择：  
1. 修改Texture类的实现（比如把）glDeleteTextures单独调用；
2. 修改Model类中加载纹理的实现，例如传入Texture的引用；
3. 使用指针。我选择了使用智能指针（相对应的地方全要改）：

```cpp
// 顺便把参数改成 `aiTextureType`(Assimp定义的用于表示Texture不同类型的枚举)
// 优化掉LearnOpenGL里那个丑陋的字符串处理
std::vector<std::shared_ptr<Texture>> Model::loadMaterialTextures(aiMaterial* mat, aiTextureType type)
{
    std::vector<std::shared_ptr<Texture>> textures;
    for (GLuint i = 0; i < mat->GetTextureCount(type); i++)
    {
        aiString str;
        mat->GetTexture(type, i, &str);
        bool canSkip = false;
        for (int j = 0; j < this->textures_loaded.size(); j++)
        {
            if (textures_loaded[j]->path == str)
            {
                textures.push_back(textures_loaded[j]);
                canSkip = true;
                break;
            }
        }
        if (!canSkip)
        {
            std::string filename = std::string(str.C_Str());
            filename = directory + '/' + filename;
            std::shared_ptr<Texture> texture = std::make_shared<Texture>(filename); // 教程里此处调用了TextureFromFile()来初始化texture，但可以用Texture的构造函数
            texture->type = type;   
            texture->path = str;
            textures.push_back(texture);
            this->textures_loaded.push_back(texture);
        }
    }
    return textures;
}
```
同理，小心其他类里的析构函数（例如Shader类可能会在析构里调用glDeleteProgram）。
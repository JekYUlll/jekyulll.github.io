+++
date = '2024-11-21T18:05:47+08:00'
draft = false
title = '表格展示 MySQL 基础数据类型'
author = 'JekYUlll'
lastmod = '2024-11-21T18:05:47+08:00'
tags = ['database', 'mysql']
categories = ['database']
+++

|数据类型|描述|存储范围/格式|示例|
|----|----|----|----|
|INT（整数型）|存储整数，有不同的字节大小来适应不同范围的整数|有TINYINT（1字节，范围 - 128到127）、SMALLINT（2字节，范围 - 32768到32767）、MEDIUMINT（3字节）、INT（4字节，范围 - 2147483648到2147483647）、BIGINT（8字节）|`age INT;`，可以存储像25这样的年龄值|
|FLOAT和DOUBLE（浮点型）|用于存储带有小数部分的数值，FLOAT精度较低，DOUBLE精度较高|FLOAT单精度浮点数，大约7位有效数字；DOUBLE双精度浮点数，大约15位有效数字|`price FLOAT;`可以存储像9.99这样的价格值，对于更高精度的科学计算可能使用`measurement DOUBLE;`|
|DECIMAL|精确的小数值存储，常用于金融等对精度要求极高的领域|格式为DECIMAL(M,D)，M是数字总位数，D是小数点后的位数|`amount DECIMAL(10,2);`可以精确存储像12345.67这样的金额，其中总共可以存储10位数字，小数点后2位|
|CHAR|定长字符串，存储固定长度的字符序列|定义时指定长度，如CHAR(10)，最多存储10个字符，不足部分用空格填充|`code CHAR(5);`可以存储像'ABCD '（注意后面有空格）这样的字符串|
|VARCHAR|可变长字符串，根据实际存储的字符长度占用空间|定义最大长度，如VARCHAR(255)，实际存储多长就占用多少空间加上1 - 2字节用于记录长度|`name VARCHAR(50);`可以存储像'John Doe'这样的名字，长度小于等于50个字符|
|TEXT|用于存储大量文本内容|有TINYTEXT、TEXT、MEDIUMTEXT和LONGTEXT，存储大小逐渐增大|`description TEXT;`可以存储一篇短文或者产品描述|
|BLOB|存储二进制大型对象，如图像、音频等|有TINYBLOB、BLOB、MEDIUMBLOB和LONGBLOB，存储大小逐渐增大|`image BLOB;`可以存储一张照片的二进制数据|
|DATE|存储日期，格式为YYYY - MM - DD|从1000 - 01 - 01到9999 - 12 - 31|`birth_date DATE;`可以存储像'2000 - 01 - 01'这样的出生日期|
|TIME|存储时间，格式为HH:MM:SS| - |`start_time TIME;`可以存储像'09:00:00'这样的开始时间|
|DATETIME|存储日期和时间，格式为YYYY - MM - DD HH:MM:SS|从1000 - 01 - 01 00:00:00到9999 - 12 - 31 23:59:59|`order_time DATETIME;`可以存储像'2024 - 01 - 01 10:30:00'这样的订单时间|
|TIMESTAMP|存储日期和时间戳，会受到时区影响|从1970 - 01 - 01 00:00:00 UTC到2038 - 01 - 19 03:14:07 UTC|`update_time TIMESTAMP;`用于记录更新时间，在不同时区设置下可能会有变化|

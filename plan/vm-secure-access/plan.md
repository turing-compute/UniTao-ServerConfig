# Feature: VM Secure Access 功能规划

## 背景

在当前设置下，通过ServerConfig创建的VM中的VM数据文件定义defaultPWD来设置VM的登录密码。
密码明码保存在json文件中有安全问题。

## 目标

解决创建的VM的安全问题

## TODO

1, 存在vm定义文件中的defaultPWD要随机生成，而不能使用默认明码ubuntu
2，UniTao-ServerConfig的部署过程要包含Key Pair的生成过程，key pair保存在运行目录中
3, 生成的密码要通过运行目录中的key pair加密保存。
4，生成解密工具，以方便人工访问
3，vm定义ssh的Access Key加入VM Host的Key，这样可以使用运行目录的私钥访问。
4，vm要求可以访问和更改一定的vm信息，但是需要提供使用VMHost公钥加密过的Access Key

## 正在进行
1, 存在vm定义文件中的defaultPWD要随机生成，而不能使用默认明码ubuntu

## 已完成
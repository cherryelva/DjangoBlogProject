# 文章收藏与个人收藏夹

## 功能

- 已登录用户可以在已发布文章的详情页收藏、取消收藏。
- 导航栏和文章详情页提供“我的收藏夹”入口，未登录访问会先跳转登录。
- 收藏夹仅显示当前账号的收藏，按收藏时间倒序排列，每页数量沿用 `PAGINATE_BY`。
- 收藏夹可以直接阅读文章、取消收藏；取消当前最后一页最后一条记录后会返回有效页。
- 重复提交收藏或取消请求不会产生重复记录，也不会反向切换状态。
- 文章转为草稿后不再展示在收藏夹中；删除文章或账号会级联删除相应收藏。

## 本地运行

在已安装项目依赖的 Python 环境中，从项目 `src` 目录执行：

```powershell
python manage.py migrate
python manage.py runserver
```

前端沿用项目的 Vite / Tailwind 样式，首次运行或更新样式后在 `src/frontend` 执行：

```powershell
npm ci
npm run build
```

本功能没有修改数据库连接配置。请在当前使用的数据库执行迁移；不要只复制 Python 代码而遗漏迁移文件。

## 自动测试

```powershell
python manage.py test blog.test_favorites blog.test_likes --verbosity 2
```

- `ArticleFavoriteUnitTests`：数据库唯一约束、多用户收藏同一文章、删除文章和用户后的级联行为。
- `ArticleFavoriteIntegrationTests`：POST 接口、幂等性、登录与 CSRF、跨账号隔离、草稿和独立页面限制、分页与边界情况、详情页状态。
- `ArticleFavoriteSystemTests`：启动真实 HTTP 测试服务器，通过 Cookie、实际登录和 CSRF 表单完成收藏、刷新、查看收藏夹、取消收藏及账号隔离流程。
- `blog.test_likes`：回归验证现有文章点赞功能。

自动系统测试使用独立测试数据库，不会向现有业务数据中添加测试账号。真实浏览器验收建议按下面的录屏流程执行。

## 录屏演示（约 2–3 分钟）

1. 简介：新增功能让读者保存感兴趣的文章，之后可以在个人收藏夹再次阅读。
2. 未登录打开文章，展示“登录后收藏”，点击进入登录页并登录。
3. 点击“收藏文章”，展示“已收藏 · 取消收藏”，刷新后状态仍然保留。
4. 打开“我的收藏夹”，展示文章标题、作者、分类和收藏时间，点击“阅读文章”。
5. 切换到另一个账号，展示其收藏夹与前一个账号隔离。
6. 回到原账号，在收藏夹取消收藏，展示空列表提示；也可以在文章详情页取消收藏。
7. 展示上述测试命令及结果，说明单元测试、集成测试、系统测试各验证了什么。

## 主要代码

- 数据模型：`blog/models.py` 中的 `ArticleFavorite`。
- 数据库迁移：`blog/migrations/0010_articlefavorite.py`。
- 接口与收藏夹查询：`blog/favorite_views.py`，路由位于 `blog/urls.py`。
- 收藏状态：`blog/views.py` 中的 `ArticleDetailView`。
- 页面：`templates/blog/article_detail.html`、`templates/blog/favorite_list.html`。
- 导航入口：`templates/share_layout/nav.html`。
- 测试：`blog/test_favorites.py`。

收藏记录以 `(user, article)` 数据库唯一约束防止重复；所有查询和删除操作都绑定 `request.user`。新增和取消只接受有 CSRF 保护的 POST 请求，收藏夹响应禁止缓存，避免共享缓存泄露个人列表。

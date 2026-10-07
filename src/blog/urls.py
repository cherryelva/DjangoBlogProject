from django.urls import path
from django.views.decorators.cache import cache_page

from . import views

app_name = "blog"
urlpatterns = [
    # 网站首页：匹配根路径，将请求交给 IndexView 视图处理
    path(
        r'',
        views.IndexView.as_view(),
        name='index'),

    # 分页首页：捕获 URL 中的整数页码，将其作为 page 参数传递给 IndexView 视图
    path(
        r'page/<int:page>/',
        views.IndexView.as_view(),
        name='index_page'),

    # 文章详情页：捕获文章的年份、月份、日期和整数 ID，用于定位指定文章
    path(
        r'article/<int:year>/<int:month>/<int:day>/<int:article_id>.html',
        views.ArticleDetailView.as_view(),
        name='detailbyid'),

    # 分类文章页：捕获 URL 中的分类名称 category_name，显示对应分类下的文章
    path(
        r'category/<slug:category_name>.html',
        views.CategoryDetailView.as_view(),
        name='category_detail'),

    # 分类文章分页页：捕获分类名称和整数页码，显示指定分类的对应分页内容
    path(
        r'category/<slug:category_name>/<int:page>.html',
        views.CategoryDetailView.as_view(),
        name='category_detail_page'),

    # 作者文章页：捕获 URL 中的作者名称 author_name，显示该作者发布的文章
    path(
        r'author/<author_name>.html',
        views.AuthorDetailView.as_view(),
        name='author_detail'),

    # 作者文章分页页：捕获作者名称和整数页码，显示该作者对应页的文章
    path(
        r'author/<author_name>/<int:page>.html',
        views.AuthorDetailView.as_view(),
        name='author_detail_page'),

    # 标签文章页：捕获 URL 中的标签名称 tag_name，显示对应标签下的文章
    path(
        r'tag/<slug:tag_name>.html',
        views.TagDetailView.as_view(),
        name='tag_detail'),

    # 标签文章分页页：捕获标签名称和整数页码，显示指定标签的对应分页内容
    path(
        r'tag/<slug:tag_name>/<int:page>.html',
        views.TagDetailView.as_view(),
        name='tag_detail_page'),

    # 文章归档页：使用缓存机制缓存页面 60 分钟，减少重复查询，提高访问速度
    path(
        'archives.html',
        cache_page(
            60 * 60)(
            views.ArchivesView.as_view()),
        name='archives'),

    # 友情链接页：将请求交给 LinkListView 视图，显示网站友情链接列表
    path(
        'links.html',
        views.LinkListView.as_view(),
        name='links'),

    # 文件上传：匹配 upload 路径，将请求交给 fileupload 函数处理
    path(
        r'upload',
        views.fileupload,
        name='upload'),

    # 清理缓存：匹配 clean 路径，将请求交给 clean_cache_view 视图处理
    path(
        r'clean',
        views.clean_cache_view,
        name='clean'),
]

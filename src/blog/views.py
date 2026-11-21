#LY: 模块功能：博客系统的核心视图层(View)，负责处理首页、文章详情（含 AI 智能导读创新功能）、分类目录、作者归档、标签列表、全文搜索及图床上传的 HTTP 请求，并将数据传递给前端模板。

import json
import logging
import os
import re
import urllib.error
import urllib.request
import uuid

from django.conf import settings
from django.core.paginator import Paginator
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, render
from django.templatetags.static import static
from django.utils import timezone
from django.utils.html import strip_tags
from django.utils.text import Truncator
from django.utils.translation import gettext_lazy as _
from django.views.decorators.csrf import csrf_exempt
from django.views.generic.detail import DetailView
from django.views.generic.list import ListView
from haystack.views import SearchView

from blog.models import Article, Category, LinkShowType, Links, Tag
from comments.forms import CommentForm
from djangoblog.mixins import (
    ArticleListMixin,
    CachedListViewMixin,
    OptimizedArticleQueryMixin,
    PageNumberMixin,
    SlugCachedMixin,
)
from djangoblog.plugin_manage import hooks
from djangoblog.plugin_manage.hook_constants import ARTICLE_CONTENT_HOOK_NAME
from djangoblog.utils import CommonMarkdown, cache, get_blog_setting, get_sha256

logger = logging.getLogger(__name__)


def generate_ai_summary(article):
    #LY: 函数级注释（创新点）：为指定文章生成 AI 智能导读摘要。优先读取缓存；若环境变量配置了大模型 API Key 则调用 LLM 生成导读；未配置密钥或网络异常时自动降级为本地关键句提取算法，确保高可用性。
    modify_ts = int(article.last_modify_time.timestamp()) if article.last_modify_time else 0
    cache_key = f'ai_summary_{article.id}_{modify_ts}'
    cached_summary = cache.get(cache_key)
    if cached_summary:
        return cached_summary

    #LY: 核心代码块：清洗 Markdown 标签提取纯文本正文，并截取前 1500 字作为模型输入以控制 Token 消耗。
    plain_text = ' '.join(strip_tags(CommonMarkdown.get_markdown(article.body)).split())
    if not plain_text:
        return "暂无文章正文内容，无法生成智能导读。"

    api_key = os.environ.get('AI_SUMMARY_API_KEY', '')
    api_base = os.environ.get('AI_SUMMARY_API_BASE', 'https://api.deepseek.com/v1/chat/completions')
    model_name = os.environ.get('AI_SUMMARY_MODEL', 'deepseek-chat')

    summary_result = ""

    #LY: 核心代码块：若环境变量配置了大模型 API Key，则通过标准 HTTP 请求调用 LLM 生成结构化导读。
    if api_key:
        try:
            prompt = (
                f"请用简洁清晰的中文为以下博客文章生成一段120字以内的核心导读摘要，提炼文章主旨与关键技术点：\n"
                f"标题：{article.title}\n正文：{plain_text[:1500]}"
            )
            payload = json.dumps({
                "model": model_name,
                "messages": [
                    {"role": "system", "content": "你是一个专业的技术博客智能导读助手。"},
                    {"role": "user", "content": prompt}
                ],
                "max_tokens": 200,
                "temperature": 0.5
            }).encode('utf-8')

            req = urllib.request.Request(
                api_base,
                data=payload,
                headers={
                    'Content-Type': 'application/json',
                    'Authorization': f'Bearer {api_key}'
                },
                method='POST'
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                res_data = json.loads(resp.read().decode('utf-8'))
                summary_result = res_data['choices'][0]['message']['content'].strip()
        except (urllib.error.URLError, KeyError, IndexError, ValueError, TimeoutError) as exc:
            logger.warning("AI 摘要 API 调用失败，已自动切换至本地提取模式: %s", exc)

    #LY: 核心代码块：本地降级兜底算法。按句号/感叹号/问号切分长句，提取首尾核心句组成结构化导读。
    if not summary_result:
        sentences = [s.strip() for s in re.split(r'[。！？.!?]', plain_text) if len(s.strip()) > 10]
        if len(sentences) >= 3:
            summary_result = f"【核心要点】{sentences[0]}。{sentences[1]}。此外，文中还探讨了{sentences[-1]}。"
        elif sentences:
            summary_result = "。".join(sentences) + "。"
        else:
            summary_result = Truncator(plain_text).chars(120, truncate='...')

    cache.set(cache_key, summary_result, 60 * 60 * 24)
    return summary_result


class ArticleListView(CachedListViewMixin, PageNumberMixin, ListView):
    #LY: 类级注释：文章列表视图基类。通过继承多个 Mixin 实现视图缓存、分页和查询优化，体现了“高内聚、低耦合”的设计原则。
    template_name = 'blog/article_index.html'
    context_object_name = 'article_list'
    page_type = ''
    paginate_by = settings.PAGINATE_BY
    page_kwarg = 'page'
    link_type = LinkShowType.L

    def get_view_cache_key(self):
        #LY: 方法级注释：获取当前分页请求的视图缓存键值。
        return self.request.GET.get('pages', '1')

    def get_context_data(self, **kwargs):
        kwargs['linktype'] = self.link_type
        return super().get_context_data(**kwargs)


class IndexView(OptimizedArticleQueryMixin, ArticleListView):
    #LY: 类级注释：博客首页视图，继承自 ArticleListView，负责展示已发布的文章列表并向模板注入全站 SEO 元数据。
    link_type = LinkShowType.I

    def get_queryset_data(self):
        #LY: 核心代码块：调用 Mixin 的优化查询方法，仅过滤出类型为文章('a')且状态为已发布('p')的数据。
        return self.get_optimized_article_queryset().filter(
            type='a', status='p'
        )

    def get_queryset_cache_key(self):
        return f'index_{self.page_number}'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        blog_setting = get_blog_setting()
        context['seo_title'] = f"{blog_setting.site_name} | {blog_setting.site_description}"
        context['seo_description'] = blog_setting.site_seo_description
        context['seo_keywords'] = blog_setting.site_keywords
        return context


class ArticleDetailView(DetailView):
    #LY: 类级注释：文章详情页面视图，核心业务视图之一，负责正文渲染、AI 智能导读生成、树形评论分页加载、上下篇导航及单页 SEO 描述自动生成。
    template_name = 'blog/article_detail.html'
    model = Article
    pk_url_kwarg = 'article_id'
    context_object_name = "article"

    def get_context_data(self, **kwargs):
        comment_form = CommentForm()
        from comments.models import Comment

        #LY: 核心代码块：使用 select_related 和 prefetch_related 进行联合预加载查询，避免渲染评论楼中楼时产生数据库 N+1 慢查询问题。
        parent_comments = Comment.objects.filter(
            article=self.object,
            parent_comment=None,
            is_enable=True
        ).select_related('author').prefetch_related(
            'comment_set__author'
        ).order_by('-id')

        article_comments = self.object.comment_list()
        blog_setting = get_blog_setting()
        paginator = Paginator(parent_comments, blog_setting.article_comment_count)

        #LY: 核心代码块：校验并规范化评论分页页码，防止非数字参数或越界页码引发分页异常。
        page = self.request.GET.get('comment_page', '1')
        page = int(page) if page.isnumeric() else 1
        page = max(1, min(page, paginator.num_pages))

        p_comments = paginator.page(page)
        next_page = p_comments.next_page_number() if p_comments.has_next() else None
        prev_page = p_comments.previous_page_number() if p_comments.has_previous() else None

        if next_page:
            kwargs['comment_next_page_url'] = (
                self.object.get_absolute_url() + f'?comment_page={next_page}#commentlist-container'
            )
        if prev_page:
            kwargs['comment_prev_page_url'] = (
                self.object.get_absolute_url() + f'?comment_page={prev_page}#commentlist-container'
            )

        kwargs.update({
            'form': comment_form,
            'article_comments': article_comments,
            'p_comments': p_comments,
            'comment_count': len(article_comments) if article_comments else 0,
            'next_article': self.object.next_article,
            'prev_article': self.object.prev_article,
        })

        context = super().get_context_data(**kwargs)
        article = self.object

        #LY: 核心代码块：将 Markdown 正文转为纯文本并截取前 150 个字符，自动生成页面 meta description。
        html_content = CommonMarkdown.get_markdown(article.body)
        description = ' '.join(strip_tags(html_content).split())
        description = Truncator(description).chars(150, truncate='...')

        tags = [tag.name.strip() for tag in article.tags.all()]
        keywords = ", ".join(tags) if tags else blog_setting.site_keywords

        context['seo_title'] = f"{article.title} | {blog_setting.site_name}"
        context['seo_description'] = description
        context['seo_keywords'] = keywords

        #LY: 核心代码块（创新点）：调用智能导读生成函数，将 AI 摘要注入文章详情页模板上下文。
        context['ai_summary'] = generate_ai_summary(article)

        #LY: 核心代码块：触发插件钩子，允许外部扩展向文章详情页上下文动态注入额外数据。
        from djangoblog.plugin_manage.hook_constants import ARTICLE_DETAIL_LOAD
        hooks.run_action(ARTICLE_DETAIL_LOAD, article=article, context=context, request=self.request)
        hooks.run_action('after_article_body_get', article=article, request=self.request)
        return context


class CategoryDetailView(SlugCachedMixin, OptimizedArticleQueryMixin, ArticleListView):
    #LY: 类级注释：分类目录归档视图，利用 SlugCachedMixin 缓存分类对象，查询当前分类及所有子分类下的已发布文章。
    page_type = "分类目录归档"
    slug_url_kwarg = 'category_name'
    slug_model = Category

    def get_queryset_data(self):
        #LY: 方法级注释：获取当前分类及其递归子分类名称列表，过滤出所属已发布文章。
        category = self.get_slug_object()
        categorynames = [c.name for c in category.get_sub_categorys()]
        return self.get_optimized_article_queryset().filter(
            category__name__in=categorynames, status='p'
        )

    def get_queryset_cache_key(self):
        category = self.get_slug_object()
        return f'category_list_{category.name}_{self.page_number}'

    def get_context_data(self, **kwargs):
        category = self.get_slug_object()
        #LY: 核心代码块：重构原有宽泛异常捕获，改用条件判断安全提取最后一级分类名称。
        categoryname = category.name.split('/')[-1] if '/' in category.name else category.name

        kwargs['page_type'] = CategoryDetailView.page_type
        kwargs['tag_name'] = categoryname

        blog_setting = get_blog_setting()
        article_count = self.get_queryset().count()
        kwargs['seo_title'] = f"{categoryname} | {blog_setting.site_name}"
        kwargs['seo_description'] = f"浏览 {categoryname} 分类下的所有文章，共 {article_count} 篇文章。"
        kwargs['seo_keywords'] = f"{categoryname}, {blog_setting.site_keywords}"

        return super().get_context_data(**kwargs)


class AuthorDetailView(OptimizedArticleQueryMixin, ArticleListView):
    #LY: 类级注释：作者文章归档视图，根据 URL 中的作者用户名筛选其发表的所有已发布文章。
    page_type = '作者文章归档'

    def get_queryset_cache_key(self):
        from uuslug import slugify
        author_name = slugify(self.kwargs['author_name'])
        return f'author_{author_name}_{self.page_number}'

    def get_queryset_data(self):
        author_name = self.kwargs['author_name']
        return self.get_optimized_article_queryset().filter(
            author__username=author_name, type='a', status='p'
        )

    def get_context_data(self, **kwargs):
        author_name = self.kwargs['author_name']
        kwargs['page_type'] = AuthorDetailView.page_type
        kwargs['tag_name'] = author_name

        blog_setting = get_blog_setting()
        article_count = self.get_queryset().count()
        kwargs['seo_title'] = f"{author_name} 的文章 | {blog_setting.site_name}"
        kwargs['seo_description'] = f"浏览 {author_name} 发表的所有文章，共 {article_count} 篇。"
        kwargs['seo_keywords'] = f"{author_name}, {blog_setting.site_keywords}"

        return super().get_context_data(**kwargs)


class TagDetailView(SlugCachedMixin, OptimizedArticleQueryMixin, ArticleListView):
    #LY: 类级注释：标签归档列表视图，展示包含指定标签的所有已发布文章。
    page_type = '分类标签归档'
    slug_url_kwarg = 'tag_name'
    slug_model = Tag

    def get_queryset_data(self):
        tag = self.get_slug_object()
        return self.get_optimized_article_queryset().filter(
            tags__name=tag.name, type='a', status='p'
        )

    def get_queryset_cache_key(self):
        tag = self.get_slug_object()
        return f'tag_{tag.name}_{self.page_number}'

    def get_context_data(self, **kwargs):
        tag = self.get_slug_object()
        kwargs['page_type'] = TagDetailView.page_type
        kwargs['tag_name'] = tag.name

        blog_setting = get_blog_setting()
        article_count = self.get_queryset().count()
        kwargs['seo_title'] = f"{tag.name} | {blog_setting.site_name}"
        kwargs['seo_description'] = f"浏览所有关于 {tag.name} 的文章，共 {article_count} 篇内容。"
        kwargs['seo_keywords'] = f"{tag.name}, {blog_setting.site_keywords}"

        return super().get_context_data(**kwargs)


class ArchivesView(OptimizedArticleQueryMixin, ArticleListView):
    #LY: 类级注释：全站文章时间轴归档视图，关闭分页一次性返回所有已发布文章。
    page_type = '文章归档'
    paginate_by = None
    page_kwarg = None
    template_name = 'blog/article_archives.html'

    def get_queryset_data(self):
        return self.get_optimized_article_queryset().filter(status='p')

    def get_queryset_cache_key(self):
        return 'archives'


class LinkListView(ListView):
    #LY: 类级注释：友情链接列表视图，查询并渲染所有处于启用状态(is_enable=True)的友情链接。
    model = Links
    template_name = 'blog/links_list.html'

    def get_queryset(self):
        return Links.objects.filter(is_enable=True)


class EsSearchView(SearchView):
    #LY: 类级注释：基于 Haystack 的全文检索视图，支持搜索结果关键词高亮与拼写纠错建议。
    def build_form(self, form_kwargs=None):
        #LY: 方法级注释：重写表单构建方法，开启搜索结果的关键词高亮(highlight)功能。
        if form_kwargs is None:
            form_kwargs = {}

        from haystack.query import SearchQuerySet
        if self.searchqueryset is None:
            sqs = SearchQuerySet().highlight()
        else:
            sqs = self.searchqueryset.highlight()

        form_kwargs['searchqueryset'] = sqs
        return super().build_form(form_kwargs=form_kwargs)

    def get_context(self):
        paginator, page = self.build_page()
        context = {
            "query": self.query,
            "form": self.form,
            "page": page,
            "paginator": paginator,
            "suggestion": None,
        }
        if hasattr(self.results, "query") and self.results.query.backend.include_spelling:
            context["suggestion"] = self.results.query.get_spelling_suggestion()
        context.update(self.extra_context())

        return context


@csrf_exempt
def fileupload(request):
    #LY: 函数级注释：图床与附件上传接口，校验请求签名并保存上传文件，若为图片则进行质量压缩。
    if request.method != 'POST':
        return HttpResponse("only for post")

    #LY: 核心代码块：校验 URL 查询参数中的双层 SHA256 签名，防止未授权接口调用。
    sign = request.GET.get('sign', None)
    if not sign or sign != get_sha256(get_sha256(settings.SECRET_KEY)):
        return HttpResponseForbidden()

    response = []
    for filename in request.FILES:
        timestr = timezone.now().strftime('%Y/%m/%d')
        imgextensions = ['jpg', 'png', 'jpeg', 'bmp']
        fname = str(filename)
        isimage = any(i in fname for i in imgextensions)

        base_dir = os.path.join(settings.STATICFILES, "files" if not isimage else "image", timestr)
        os.makedirs(base_dir, exist_ok=True)

        #LY: 核心代码块：使用 os.path.normpath 规范化路径并校验目录前缀，防范目录穿越安全漏洞。
        savepath = os.path.normpath(
            os.path.join(base_dir, f"{uuid.uuid4().hex}{os.path.splitext(filename)[-1]}")
        )
        if not savepath.startswith(base_dir):
            return HttpResponse("only for post")

        with open(savepath, 'wb+') as wfile:
            for chunk in request.FILES[filename].chunks():
                wfile.write(chunk)

        if isimage:
            from PIL import Image
            image = Image.open(savepath)
            image.save(savepath, quality=20, optimize=True)

        url = static(savepath)
        response.append(url)
    return HttpResponse(response)


# ===== 错误处理视图 =====
# 注意：这些函数保留是为了向后兼容，实际实现已移动至 djangoblog.error_views
from djangoblog.error_views import (
    page_not_found_view,
    server_error_view,
    permission_denied_view
)


def clean_cache_view(request):
    #LY: 函数级注释：手动清理系统全局缓存的辅助视图接口。
    cache.clear()
    return HttpResponse('ok')
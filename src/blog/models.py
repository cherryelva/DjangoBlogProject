import logging
import re
from abc import abstractmethod

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from django.utils.timezone import now
from django.utils.translation import gettext_lazy as _
from mdeditor.fields import MDTextField
from uuslug import slugify

from djangoblog.utils import cache_decorator, cache
from djangoblog.utils import get_current_site
from djangoblog.constants import CacheTimeout, CacheKey

logger = logging.getLogger(__name__)


# ==========================================
# 链接显示类型枚举类
# ==========================================
class LinkShowType(models.TextChoices):
    I = ('i', _('index'))  # 首页显示
    L = ('l', _('list'))  # 列表页显示
    P = ('p', _('post'))  # 文章页显示
    A = ('a', _('all'))  # 全站显示
    S = ('s', _('slide'))  # 侧边栏显示


# ==========================================
# 基础数据模型 (BaseModel)
# 作用：这是一个抽象基类，其他数据模型（如文章、分类）都会继承它。
# 它统一规定了每个表都必须有主键(id)、创建时间和最后修改时间，避免重复写代码。
# ==========================================
class BaseModel(models.Model):
    id = models.AutoField(primary_key=True)
    creation_time = models.DateTimeField(_('creation time'), default=now)
    last_modify_time = models.DateTimeField(_('modify time'), default=now)

    def save(self, *args, **kwargs):
        # 重写保存逻辑：如果是仅仅更新文章浏览量(views)，则直接使用 update 方法，避免全字段更新，提升数据库性能
        is_update_views = isinstance(
            self,
            Article) and 'update_fields' in kwargs and kwargs['update_fields'] == ['views']
        if is_update_views:
            Article.objects.filter(pk=self.pk).update(views=self.views)
        else:
            # 如果包含 slug (网址别名) 字段，则自动根据标题生成友好的 URL 字符串
            if 'slug' in self.__dict__:
                slug = getattr(
                    self, 'title') if 'title' in self.__dict__ else getattr(
                    self, 'name')
                setattr(self, 'slug', slugify(slug))
            super().save(*args, **kwargs)

    def get_full_url(self):
        # 拼接当前站点的完整 URL 路径
        site = get_current_site().domain
        url = "https://{site}{path}".format(site=site,
                                            path=self.get_absolute_url())
        return url

    class Meta:
        # abstract = True 告诉 Django 这是一个抽象类，不要在数据库中为它单独建表
        abstract = True

    @abstractmethod
    def get_absolute_url(self):
        pass


# ==========================================
# 核心业务：文章数据模型 (Article)
# 作用：存储博客文章的核心内容，涉及大量与其他表的外键交互。
# ==========================================
class Article(BaseModel):
    """文章"""
    STATUS_CHOICES = (
        ('d', _('Draft')),  # 草稿状态
        ('p', _('Published')),  # 已发布状态
    )
    COMMENT_STATUS = (
        ('o', _('Open')),  # 允许评论
        ('c', _('Close')),  # 关闭评论
    )
    TYPE = (
        ('a', _('Article')),  # 普通文章
        ('p', _('Page')),  # 独立页面(如"关于我")
    )
    title = models.CharField(_('title'), max_length=200, unique=True)
    # 使用 Markdown 富文本字段存储正文
    body = MDTextField(_('body'))
    pub_time = models.DateTimeField(
        _('publish time'), blank=False, null=False, default=now)
    status = models.CharField(
        _('status'),
        max_length=1,
        choices=STATUS_CHOICES,
        default='p')
    comment_status = models.CharField(
        _('comment status'),
        max_length=1,
        choices=COMMENT_STATUS,
        default='o')
    type = models.CharField(_('type'), max_length=1, choices=TYPE, default='a')
    views = models.PositiveIntegerField(_('views'), default=0)  # 浏览量统计

    # 【外键关联】文章作者，关联到 Django 自带的 User 表 (多对一)
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_('author'),
        blank=False,
        null=False,
        on_delete=models.CASCADE)  # CASCADE表示用户被删时，其文章也会被级联删除

    article_order = models.IntegerField(
        _('order'), blank=False, null=False, default=0)  # 置顶或排序权重
    show_toc = models.BooleanField(_('show toc'), blank=False, null=False, default=False)

    # 【外键关联】文章所属分类 (多对一)
    category = models.ForeignKey(
        'Category',
        verbose_name=_('category'),
        on_delete=models.CASCADE,
        blank=False,
        null=False)

    # 【多对多关联】文章包含的标签，一篇文章可以有多个标签，一个标签下有多篇文章
    tags = models.ManyToManyField('Tag', verbose_name=_('tag'), blank=True)

    def body_to_string(self):
        return self.body

    def __str__(self):
        return self.title

    class Meta:
        ordering = ['-article_order', '-pub_time']  # 默认按置顶权重和发布时间倒序排列
        verbose_name = _('article')
        verbose_name_plural = verbose_name
        get_latest_by = 'id'

        # 【性能优化亮点】在此定义了多个数据库组合索引，极大提升了大量数据下的查询速度
        indexes = [
            # 优化列表查询：type + status + pub_time组合索引
            models.Index(fields=['type', 'status', '-pub_time'], name='idx_type_status_pub'),
            # 优化热门文章查询：status + views组合索引
            models.Index(fields=['status', '-views'], name='idx_status_views'),
            # 优化作者文章查询：author + status + type组合索引
            models.Index(fields=['author', 'status', 'type'], name='idx_author_status_type'),
            # 优化分类查询：category + status组合索引
            models.Index(fields=['category', 'status'], name='idx_category_status'),
        ]

    def get_absolute_url(self):
        # 反向解析路由：根据 ID 和时间生成具体的文章网址
        return reverse('blog:detailbyid', kwargs={
            'article_id': self.id,
            'year': self.creation_time.year,
            'month': self.creation_time.month,
            'day': self.creation_time.day
        })

    # 使用缓存装饰器提升性能，将分类树缓存10小时，减少数据库连表查询压力
    @cache_decorator(CacheTimeout.HOUR_10)
    def get_category_tree(self):
        tree = self.category.get_category_tree()
        names = list(map(lambda c: (c.name, c.get_absolute_url()), tree))
        return names

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)

    def viewed(self):
        # 浏览量自增逻辑
        self.views += 1
        self.save(update_fields=['views'])

    def comment_list(self):
        # 获取该文章下的所有有效评论，并使用 Redis/Memcached 缓存机制来加速读取
        cache_key = CacheKey.ARTICLE_COMMENTS.format(article_id=self.id)
        value = cache.get(cache_key)
        if value:
            logger.info(f'Cache HIT: article comments (id={self.id})')
            return value
        else:
            comments = self.comment_set.filter(is_enable=True).order_by('-id')
            cache.set(cache_key, comments, CacheTimeout.HOUR_10)
            logger.info(f'Cache MISS: article comments (id={self.id})')
            return comments

    def get_admin_url(self):
        # 获取该文章在 Django 后台管理系统中的编辑页面链接
        info = (self._meta.app_label, self._meta.model_name)
        return reverse('admin:%s_%s_change' % info, args=(self.pk,))

    @cache_decorator(expiration=CacheTimeout.HOUR_10)
    def next_article(self):
        # 逻辑算法：获取下一篇已发布的文章（基于ID大于当前ID）
        return Article.objects.filter(
            id__gt=self.id, status='p').order_by('id').first()

    @cache_decorator(expiration=CacheTimeout.HOUR_10)
    def prev_article(self):
        # 逻辑算法：获取前一篇已发布的文章
        return Article.objects.filter(id__lt=self.id, status='p').first()

    def get_first_image_url(self):
        """
        利用正则表达式，从 Markdown 格式的文章正文中提取第一张图片的 URL 作为文章封面
        """
        match = re.search(r'!\[.*?\]\((.+?)\)', self.body)
        if match:
            return match.group(1)
        return ""


# ==========================================
# 业务模型：文章分类 (Category)
# 作用：支持多级分类目录结构。
# ==========================================
class Category(BaseModel):
    """文章分类"""
    name = models.CharField(_('category name'), max_length=30, unique=True)
    # 【自关联外键】父级分类。允许为空(null=True)，形成一棵无限极分类树
    parent_category = models.ForeignKey(
        'self',
        verbose_name=_('parent category'),
        blank=True,
        null=True,
        on_delete=models.CASCADE)
    slug = models.SlugField(default='no-slug', max_length=60, blank=True)  # 用于 URL 优化的别名
    index = models.IntegerField(default=0, verbose_name=_('index'))  # 排序权重

    class Meta:
        ordering = ['-index']
        verbose_name = _('category')
        verbose_name_plural = verbose_name

    def get_absolute_url(self):
        return reverse(
            'blog:category_detail', kwargs={
                'category_name': self.slug})

    def __str__(self):
        return self.name

    @cache_decorator(CacheTimeout.HOUR_10)
    def get_category_tree(self):
        """
        核心算法：利用递归函数 (parse)，向上追溯获得当前分类目录的所有父级节点路径
        """
        categorys = []

        def parse(category):
            categorys.append(category)
            if category.parent_category:
                parse(category.parent_category)

        parse(self)
        return categorys

    @cache_decorator(CacheTimeout.HOUR_10)
    def get_sub_categorys(self):
        """
        核心算法：利用递归遍历，向下查找当前分类目录下的所有子分类集合
        """
        categorys = []
        all_categorys = Category.objects.all()

        def parse(category):
            if category not in categorys:
                categorys.append(category)
            childs = all_categorys.filter(parent_category=category)
            for child in childs:
                if category not in categorys:
                    categorys.append(child)
                parse(child)

        parse(self)
        return categorys


# ==========================================
# 业务模型：文章标签 (Tag)
# ==========================================
class Tag(BaseModel):
    """文章标签"""
    name = models.CharField(_('tag name'), max_length=30, unique=True)
    slug = models.SlugField(default='no-slug', max_length=60, blank=True)

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('blog:tag_detail', kwargs={'tag_name': self.slug})

    @cache_decorator(CacheTimeout.HOUR_10)
    def get_article_count(self):
        # 统计该标签下有多少篇不重复的文章
        return Article.objects.filter(tags__name=self.name).distinct().count()

    class Meta:
        ordering = ['name']
        verbose_name = _('tag')
        verbose_name_plural = verbose_name


# ==========================================
# 辅助模型：友情链接 (Links)
# ==========================================
class Links(models.Model):
    """友情链接"""

    name = models.CharField(_('link name'), max_length=30, unique=True)
    link = models.URLField(_('link'))
    sequence = models.IntegerField(_('order'), unique=True)
    is_enable = models.BooleanField(
        _('is show'), default=True, blank=False, null=False)
    show_type = models.CharField(
        _('show type'),
        max_length=1,
        choices=LinkShowType.choices,
        default=LinkShowType.I)
    creation_time = models.DateTimeField(_('creation time'), default=now)
    last_mod_time = models.DateTimeField(_('modify time'), default=now)

    class Meta:
        ordering = ['sequence']
        verbose_name = _('link')
        verbose_name_plural = verbose_name

    def __str__(self):
        return self.name


# ==========================================
# 辅助模型：侧边栏组件 (SideBar)
# ==========================================
class SideBar(models.Model):
    """侧边栏,可以动态后台配置并展示一些html内容"""
    name = models.CharField(_('title'), max_length=100)
    content = models.TextField(_('content'))
    sequence = models.IntegerField(_('order'), unique=True)
    is_enable = models.BooleanField(_('is enable'), default=True)
    creation_time = models.DateTimeField(_('creation time'), default=now)
    last_mod_time = models.DateTimeField(_('modify time'), default=now)

    class Meta:
        ordering = ['sequence']
        verbose_name = _('sidebar')
        verbose_name_plural = verbose_name

    def __str__(self):
        return self.name


# ==========================================
# 系统模型：全局博客配置 (BlogSettings)
# 作用：通过单例模式存储站点的全局设置（SEO、主题颜色、广告代码等），方便后台直接修改而无需动代码。
# ==========================================
class BlogSettings(models.Model):
    """blog的配置"""

    COLOR_SCHEMES = (
        ('purple', _('紫色主题 - Purple Dream')),
        ('blue', _('蓝色主题 - Ocean Blue')),
        ('green', _('绿色主题 - Forest Green')),
        ('orange', _('橙色主题 - Sunset Orange')),
        ('pink', _('粉色主题 - Cherry Blossom')),
        ('red', _('红色主题 - Ruby Red')),
        ('indigo', _('靛蓝主题 - Midnight Indigo')),
        ('teal', _('青色主题 - Teal Wave')),
    )

    site_name = models.CharField(
        _('site name'),
        max_length=200,
        null=False,
        blank=False,
        default='')
    site_description = models.TextField(
        _('site description'),
        max_length=1000,
        null=False,
        blank=False,
        default='')
    site_seo_description = models.TextField(
        _('site seo description'), max_length=1000, null=False, blank=False, default='')
    site_keywords = models.TextField(
        _('site keywords'),
        max_length=1000,
        null=False,
        blank=False,
        default='')
    article_sub_length = models.IntegerField(_('article sub length'), default=300)
    sidebar_article_count = models.IntegerField(_('sidebar article count'), default=10)
    sidebar_comment_count = models.IntegerField(_('sidebar comment count'), default=5)
    article_comment_count = models.IntegerField(_('article comment count'), default=5)
    show_google_adsense = models.BooleanField(_('show adsense'), default=False)
    google_adsense_codes = models.TextField(
        _('adsense code'), max_length=2000, null=True, blank=True, default='')
    open_site_comment = models.BooleanField(_('open site comment'), default=True)
    color_scheme = models.CharField(
        _('配色方案'),
        max_length=20,
        choices=COLOR_SCHEMES,
        default='purple',
        help_text=_('选择网站的主题配色方案'))
    global_header = models.TextField("公共头部", null=True, blank=True, default='')
    global_footer = models.TextField("公共尾部", null=True, blank=True, default='')
    beian_code = models.CharField(
        '备案号',
        max_length=2000,
        null=True,
        blank=True,
        default='')
    analytics_code = models.TextField(
        "网站统计代码",
        max_length=1000,
        null=False,
        blank=False,
        default='')
    show_gongan_code = models.BooleanField(
        '是否显示公安备案号', default=False, null=False)
    gongan_beiancode = models.TextField(
        '公安备案号',
        max_length=2000,
        null=True,
        blank=True,
        default='')
    comment_need_review = models.BooleanField(
        '评论是否需要审核', default=False, null=False)

    class Meta:
        verbose_name = _('Website configuration')
        verbose_name_plural = verbose_name

    def __str__(self):
        return self.site_name

    def clean(self):
        # 单例模式校验：确保整个系统中只能存在一条配置数据，防止后台被误创建多份配置
        if BlogSettings.objects.exclude(id=self.id).count():
            raise ValidationError(_('There can only be one configuration'))

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # 只要配置项被修改保存，自动清空系统缓存，使新配置即时生效
        from djangoblog.utils import cache
        cache.clear()
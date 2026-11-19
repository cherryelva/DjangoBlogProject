from django.core.cache import cache
from django.test import Client, TestCase
from django.test import LiveServerTestCase
from django.urls import reverse

from accounts.models import BlogUser
from blog.models import Article, BlogSettings, Category
from djangoblog.admin_site import admin_site


class ArticleLikeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = BlogUser.objects.create_user(username='like-reader', password='test-like-pass')
        cls.other = BlogUser.objects.create_user(username='other-reader', password='test-like-pass')
        cls.category = Category.objects.create(name='Like-tests')
        cls.article = Article.objects.create(title='Like test article', body='Article body',
                                             author=cls.user, category=cls.category)
        BlogSettings.objects.get_or_create(pk=1)

    def setUp(self):
        cache.clear()
        self.url = reverse('blog:like-article', args=[self.article.pk])
        self.client.force_login(self.user)

    def assert_likes(self, count):
        self.article.refresh_from_db()
        self.assertEqual(self.article.like_count, count)
        self.assertEqual(self.article.liked_users.count(), count)

    def test_model_toggle_adds_and_removes_like(self):
        self.assertTrue(self.article.toggle_like(self.user))
        self.assert_likes(1)
        self.assertFalse(self.article.toggle_like(self.user))
        self.assert_likes(0)

    def test_model_rejects_anonymous_user(self):
        from django.contrib.auth.models import AnonymousUser
        with self.assertRaises(ValueError):
            self.article.toggle_like(AnonymousUser())
        self.assert_likes(0)

    def test_post_likes_and_redirects_to_article(self):
        response = self.client.post(self.url)
        self.assertRedirects(response, self.article.get_absolute_url(), fetch_redirect_response=False)
        self.assert_likes(1)
        self.assertTrue(self.article.liked_users.filter(pk=self.user.pk).exists())

    def test_second_post_cancels_like(self):
        self.client.post(self.url)
        self.client.post(self.url)
        self.assert_likes(0)

    def test_users_have_independent_likes(self):
        self.client.post(self.url)
        self.client.force_login(self.other)
        self.client.post(self.url)
        self.assert_likes(2)
        self.client.force_login(self.user)
        self.client.post(self.url)
        self.assert_likes(1)
        self.assertTrue(self.article.liked_users.filter(pk=self.other.pk).exists())

    def test_get_does_not_modify_likes(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.assert_likes(0)

    def test_anonymous_post_goes_to_login(self):
        self.client.logout()
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(reverse('accounts:login') + '?next='))
        self.assert_likes(0)

    def test_post_requires_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post(self.url).status_code, 403)
        self.assert_likes(0)

    def test_missing_article_returns_404(self):
        self.assertEqual(self.client.post(reverse('blog:like-article', args=[999999])).status_code, 404)

    def test_draft_cannot_be_liked(self):
        Article.objects.filter(pk=self.article.pk).update(status='d')
        self.assertEqual(self.client.post(self.url).status_code, 404)
        self.assert_likes(0)

    def test_stale_counter_is_reconciled(self):
        Article.objects.filter(pk=self.article.pk).update(like_count=10)
        self.client.post(self.url)
        self.assert_likes(1)

    def test_detail_renders_like_form_and_cancel_state(self):
        response = self.client.get(self.article.get_absolute_url())
        self.assertContains(response, 'action="' + self.url + '"')
        self.assertContains(response, 'aria-pressed="false"')
        self.client.post(self.url)
        response = self.client.get(self.article.get_absolute_url())
        self.assertContains(response, 'aria-pressed="true"')
        self.assertContains(response, '取消点赞')
        self.assertEqual(response.context['article'].like_count, 1)

    def test_anonymous_detail_offers_login(self):
        self.client.logout()
        response = self.client.get(self.article.get_absolute_url())
        self.assertContains(response, '登录后点赞')

    def test_admin_exposes_readonly_like_statistics(self):
        model_admin = admin_site._registry[Article]
        self.assertIn('like_count', model_admin.list_display)
        self.assertIn('like_count', model_admin.readonly_fields)
        self.assertIn('liked_users', model_admin.readonly_fields)


class ArticleLikeHTTPTests(LiveServerTestCase):
    """Exercise the actual HTTP server, cookies, login and CSRF together."""

    def setUp(self):
        import http.cookiejar
        import urllib.request
        cache.clear()
        self.user = BlogUser.objects.create_user(username='http-reader', password='http-test-password')
        category = Category.objects.create(name='HTTP-tests')
        self.article = Article.objects.create(title='HTTP like article', body='HTTP body',
                                             author=self.user, category=category)
        BlogSettings.objects.get_or_create(pk=1)
        self.cookies = http.cookiejar.CookieJar()
        self.http = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies))

    def get_page(self, path):
        with self.http.open(self.live_server_url + path, timeout=15) as response:
            return response.read().decode('utf-8'), response.url

    def post_form(self, path, fields):
        from urllib.parse import urlencode
        with self.http.open(self.live_server_url + path, urlencode(fields).encode(), timeout=15) as response:
            return response.read().decode('utf-8'), response.url

    def csrf(self):
        return next(cookie.value for cookie in self.cookies if cookie.name == 'csrftoken')

    def test_login_like_reload_and_cancel_over_http(self):
        path = self.article.get_absolute_url()
        self.get_page(reverse('accounts:login'))
        self.post_form(reverse('accounts:login'), {
            'username': 'http-reader', 'password': 'http-test-password',
            'csrfmiddlewaretoken': self.csrf(), 'next': path,
        })
        like_path = reverse('blog:like-article', args=[self.article.pk])
        html, url = self.post_form(like_path, {'csrfmiddlewaretoken': self.csrf()})
        self.assertEqual(url, self.live_server_url + path)
        self.assertIn('aria-pressed="true"', html)
        html, _ = self.get_page(path)
        self.assertIn('aria-pressed="true"', html)
        html, _ = self.post_form(like_path, {'csrfmiddlewaretoken': self.csrf()})
        self.assertIn('aria-pressed="false"', html)
        self.article.refresh_from_db()
        self.assertEqual(self.article.like_count, 0)
        self.assertFalse(self.article.liked_users.exists())

    def test_anonymous_article_over_http(self):
        html, _ = self.get_page(self.article.get_absolute_url())
        self.assertIn('登录后点赞', html)

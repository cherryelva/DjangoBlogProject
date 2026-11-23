"""Unit, integration and real HTTP tests for private article favorites."""
import http.cookiejar
import urllib.error
import urllib.request
from urllib.parse import urlencode

from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.test import Client, LiveServerTestCase, TestCase, override_settings
from django.urls import reverse

from accounts.models import BlogUser
from blog.models import Article, ArticleFavorite, BlogSettings, Category


def create_data():
    user = BlogUser.objects.create_user(username='collector', password='Favorite-test-123')
    other = BlogUser.objects.create_user(username='other-collector', password='Favorite-test-123')
    category = Category.objects.create(name='Favorite tests')
    article = Article.objects.create(
        title='Article to save', body='A useful article.', author=user, category=category)
    BlogSettings.objects.get_or_create(pk=1)
    return user, other, article


class ArticleFavoriteUnitTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user, cls.other, cls.article = create_data()

    def test_duplicate_is_rejected_by_database(self):
        ArticleFavorite.objects.create(user=self.user, article=self.article)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ArticleFavorite.objects.create(user=self.user, article=self.article)
        self.assertEqual(ArticleFavorite.objects.count(), 1)

    def test_two_users_can_save_same_article(self):
        for user in (self.user, self.other):
            ArticleFavorite.objects.create(user=user, article=self.article)
        self.assertEqual(ArticleFavorite.objects.count(), 2)

    def test_deleting_article_removes_its_favorites(self):
        ArticleFavorite.objects.create(user=self.user, article=self.article)
        self.article.delete()
        self.assertFalse(ArticleFavorite.objects.exists())

    def test_deleting_reader_preserves_other_readers_favorites(self):
        ArticleFavorite.objects.create(user=self.other, article=self.article)
        ArticleFavorite.objects.create(user=self.user, article=self.article)
        self.other.delete()
        self.assertEqual(list(ArticleFavorite.objects.values_list('user_id', flat=True)), [self.user.pk])


class ArticleFavoriteIntegrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user, cls.other, cls.article = create_data()

    def setUp(self):
        cache.clear()
        self.client.force_login(self.user)
        self.add_url = reverse('blog:favorite-add', args=[self.article.pk])
        self.remove_url = reverse('blog:favorite-remove', args=[self.article.pk])
        self.list_url = reverse('blog:favorite-list')

    def test_add_redirects_and_is_idempotent(self):
        for _ in range(2):
            self.assertRedirects(self.client.post(self.add_url), self.article.get_absolute_url(),
                                 fetch_redirect_response=False)
        self.assertEqual(ArticleFavorite.objects.get().user, self.user)

    def test_remove_is_idempotent(self):
        self.client.post(self.add_url)
        for _ in range(2):
            self.assertEqual(self.client.post(self.remove_url).status_code, 302)
        self.assertFalse(ArticleFavorite.objects.exists())

    def test_removing_cannot_delete_another_users_record(self):
        ArticleFavorite.objects.create(user=self.other, article=self.article)
        self.client.post(self.add_url)
        self.client.post(self.remove_url, {'user_id': self.other.pk})
        self.assertEqual(ArticleFavorite.objects.get().user, self.other)

    def test_anonymous_requests_redirect_to_login(self):
        self.client.logout()
        for url, method in ((self.list_url, self.client.get),
                            (self.add_url, self.client.post),
                            (self.remove_url, self.client.post)):
            with self.subTest(url=url):
                response = method(url)
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response.url.startswith(reverse('accounts:login') + '?next='))
        self.assertFalse(ArticleFavorite.objects.exists())

    def test_get_does_not_change_favorites(self):
        self.assertEqual(self.client.get(self.add_url).status_code, 405)
        self.client.post(self.add_url)
        self.assertEqual(self.client.get(self.remove_url).status_code, 405)
        self.assertEqual(ArticleFavorite.objects.count(), 1)

    def test_both_actions_enforce_csrf(self):
        secure_client = Client(enforce_csrf_checks=True)
        secure_client.force_login(self.user)
        self.assertEqual(secure_client.post(self.add_url).status_code, 403)
        self.client.post(self.add_url)
        self.assertEqual(secure_client.post(self.remove_url).status_code, 403)
        self.assertEqual(ArticleFavorite.objects.count(), 1)

    def test_missing_articles_return_404(self):
        for action in ('favorite-add', 'favorite-remove'):
            self.assertEqual(self.client.post(reverse('blog:' + action, args=[999999])).status_code, 404)

    def test_drafts_and_standalone_pages_cannot_be_saved(self):
        for changes in ({'status': 'd'}, {'status': 'p', 'type': 'p'}):
            Article.objects.filter(pk=self.article.pk).update(**changes)
            self.assertEqual(self.client.post(self.add_url).status_code, 404)
        self.assertFalse(ArticleFavorite.objects.exists())

    def test_list_is_private_and_ignores_other_user_parameter(self):
        self.client.post(self.add_url)
        self.assertContains(self.client.get(self.list_url), self.article.title)
        self.client.force_login(self.other)
        response = self.client.get(self.list_url, {'user_id': self.user.pk})
        self.assertEqual(response.context['favorite_count'], 0)
        self.assertContains(response, '还没有收藏文章')
        self.assertNotContains(response, self.article.title)
        self.assertIn('no-store', response['Cache-Control'])

    def test_unpublished_article_is_hidden_but_can_be_removed(self):
        self.client.post(self.add_url)
        Article.objects.filter(pk=self.article.pk).update(status='d')
        self.assertEqual(self.client.get(self.list_url).context['favorite_count'], 0)
        self.client.post(self.remove_url, {'return_to': 'favorites'})
        self.assertFalse(ArticleFavorite.objects.exists())

    @override_settings(PAGINATE_BY=1)
    def test_order_pagination_and_removing_last_page(self):
        self.client.post(self.add_url)
        second = Article.objects.create(title='Newer saved article', body='Second',
                                        author=self.user, category=self.article.category)
        self.client.post(reverse('blog:favorite-add', args=[second.pk]))
        response = self.client.get(self.list_url)
        self.assertEqual(response.context['page_obj'][0].article, second)
        self.assertContains(response, '下一页')
        response = self.client.get(self.list_url, {'page': 2})
        self.assertEqual(response.context['page_obj'][0].article, self.article)
        response = self.client.post(self.remove_url, {'return_to': 'favorites', 'page': 2}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['page_obj'].number, 1)
        self.assertEqual(response.context['favorite_count'], 1)

    def test_invalid_page_is_handled(self):
        for page in ('bad', '-1', '9999'):
            self.assertEqual(self.client.get(self.list_url, {'page': page}).status_code, 200)

    def test_supplied_redirect_cannot_leave_site(self):
        response = self.client.post(self.remove_url, {
            'return_to': 'https://example.org/', 'next': 'https://example.org/'})
        self.assertEqual(response.url, self.article.get_absolute_url())

    def test_detail_state_survives_reload_and_is_per_user(self):
        url = self.article.get_absolute_url()
        self.assertContains(self.client.get(url), '☆ 收藏文章')
        self.client.post(self.add_url)
        response = self.client.get(url)
        self.assertContains(response, '★ 已收藏 · 取消收藏')
        self.assertContains(response, 'action="' + self.remove_url + '"')
        self.assertContains(response, 'AI 智能导读')
        self.assertTrue(response.context['ai_summary'])
        self.client.force_login(self.other)
        self.assertContains(self.client.get(url), '☆ 收藏文章')
        self.client.logout()
        self.assertContains(self.client.get(url), '登录后收藏')


class ArticleFavoriteSystemTests(LiveServerTestCase):
    """Actual HTTP server with login cookies, CSRF, templates and redirects."""

    def setUp(self):
        cache.clear()
        self.user, self.other, self.article = create_data()
        self.cookies = http.cookiejar.CookieJar()
        self.http = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies))

    def request(self, path, fields=None):
        data = None if fields is None else urlencode(fields).encode()
        with self.http.open(self.live_server_url + path, data, timeout=20) as response:
            return response.read().decode('utf-8'), response.url

    def csrf(self):
        return next(cookie.value for cookie in self.cookies if cookie.name == 'csrftoken')

    def login(self, username):
        self.request(reverse('accounts:login'))
        self.request(reverse('accounts:login'), {
            'username': username, 'password': 'Favorite-test-123',
            'csrfmiddlewaretoken': self.csrf(), 'next': self.article.get_absolute_url(),
        })
        self.assertTrue(any(cookie.name == 'sessionid' for cookie in self.cookies))

    def test_login_save_reload_collection_and_remove(self):
        self.login(self.user.username)
        add_url = reverse('blog:favorite-add', args=[self.article.pk])
        html, _ = self.request(add_url, {'csrfmiddlewaretoken': self.csrf()})
        self.assertIn('已收藏 · 取消收藏', html)
        html, _ = self.request(self.article.get_absolute_url())
        self.assertIn('已收藏 · 取消收藏', html)
        list_url = reverse('blog:favorite-list')
        html, _ = self.request(list_url)
        self.assertIn(self.article.title, html)
        html, final_url = self.request(reverse('blog:favorite-remove', args=[self.article.pk]), {
            'csrfmiddlewaretoken': self.csrf(), 'return_to': 'favorites', 'page': '1',
        })
        self.assertIn(list_url, final_url)
        self.assertIn('还没有收藏文章', html)
        self.assertFalse(ArticleFavorite.objects.exists())

    def test_anonymous_collection_requires_login(self):
        _, final_url = self.request(reverse('blog:favorite-list'))
        self.assertIn(reverse('accounts:login') + '?next=', final_url)

    def test_account_switch_does_not_expose_favorites(self):
        ArticleFavorite.objects.create(user=self.user, article=self.article)
        self.login(self.other.username)
        html, _ = self.request(reverse('blog:favorite-list'))
        self.assertIn('还没有收藏文章', html)
        self.assertNotIn(self.article.title, html)

    def test_http_post_without_csrf_is_rejected(self):
        self.login(self.user.username)
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.request(reverse('blog:favorite-add', args=[self.article.pk]), {})
        self.assertEqual(raised.exception.code, 403)
        self.assertFalse(ArticleFavorite.objects.exists())

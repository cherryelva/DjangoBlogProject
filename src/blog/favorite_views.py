"""Private favorites: explicit, idempotent add/remove actions using POST."""
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from .models import Article, ArticleFavorite


@never_cache
@login_required(login_url='accounts:login')
@require_GET
def favorite_list(request):
    favorites = ArticleFavorite.objects.filter(
        user=request.user, article__status='p', article__type='a',
    ).select_related('article__author', 'article__category')
    page = Paginator(favorites, settings.PAGINATE_BY).get_page(request.GET.get('page'))
    return render(request, 'blog/favorite_list.html', {
        'page_obj': page,
        'favorite_count': page.paginator.count,
        'nav_active': 'favorites',
    })


@require_POST
@login_required(login_url='accounts:login')
def favorite_add(request, article_id):
    article = get_object_or_404(Article, pk=article_id, status='p', type='a')
    ArticleFavorite.objects.get_or_create(user=request.user, article=article)
    return redirect(article.get_absolute_url())


@require_POST
@login_required(login_url='accounts:login')
def favorite_remove(request, article_id):
    article = get_object_or_404(Article, pk=article_id)
    ArticleFavorite.objects.filter(user=request.user, article=article).delete()
    if request.POST.get('return_to') == 'favorites':
        # Use a fixed internal destination instead of trusting a supplied URL.
        query = urlencode({'page': request.POST.get('page', '1')})
        return redirect(reverse('blog:favorite-list') + '?' + query)
    return redirect(article.get_absolute_url())

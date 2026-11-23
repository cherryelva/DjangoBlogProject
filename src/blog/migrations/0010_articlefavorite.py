from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('blog', '0008_blogsettings_color_scheme'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ArticleFavorite',
            fields=[
                ('id', models.AutoField(primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='收藏时间')),
                ('article', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='favorites', to='blog.article', verbose_name='收藏文章')),
                ('user', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='article_favorites', to=settings.AUTH_USER_MODEL,
                    verbose_name='收藏用户')),
            ],
            options={
                'verbose_name': '文章收藏',
                'verbose_name_plural': '文章收藏',
                'ordering': ['-created_at', '-pk'],
                'constraints': [models.UniqueConstraint(
                    fields=('user', 'article'), name='unique_user_article_favorite')],
            },
        ),
    ]

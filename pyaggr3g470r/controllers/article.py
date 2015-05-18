import re
import logging
from sqlalchemy import func

from bootstrap import db
from .abstract import AbstractController
from pyaggr3g470r.controllers import FeedController
from pyaggr3g470r.models import Article

logger = logging.getLogger(__name__)


class ArticleController(AbstractController):
    _db_cls = Article

    def get(self, **filters):
        article = super(ArticleController, self).get(**filters)
        if not article.readed:
            self.update({'id': article.id}, {'readed': True})
        return article

    def challenge(self, ids):
        """Will return each id that wasn't found in the database."""
        for id_ in ids:
            if self.read(**id_).first():
                continue
            yield id_

    def get_unread(self):
        return dict(db.session.query(Article.feed_id, func.count(Article.id))
                       .filter(*self._to_filters(readed=False,
                                                 user_id=self.user_id))
                       .group_by(Article.feed_id).all())

    def create(self, **attrs):
        assert 'feed_id' in attrs
        feed = FeedController(
                attrs.get('user_id', self.user_id)).get(id=attrs['feed_id'])
        if 'user_id' in attrs:
            assert feed.user_id == attrs['user_id'] or self.user_id is None
        attrs['user_id'] = feed.user_id
        if not feed.filters:
            return super().create(**attrs)
        for filter_ in feed.filters:
            match = False
            if filter_.get('type') == 'regex':
                match = re.match(filter_['pattern'], attrs.get('title', ''))
            elif filter_.get('type') == 'simple match':
                match = filter_['pattern'] in attrs.get('title', '')
            take_action = match and filter_.get('action on') == 'match' \
                    or not match and filter_.get('action on') == 'no match'

            if not take_action:
                continue

            if filter_.get('action') == 'mark as read':
                attrs['readed'] = True
                logger.warn("article %s will be created as read",
                            attrs['link'])
            elif filter_.get('action') == 'mark as favorite':
                attrs['like'] = True
                logger.warn("article %s will be created as liked",
                            attrs['link'])

        return super().create(**attrs)



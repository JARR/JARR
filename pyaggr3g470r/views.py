#! /usr/bin/env python
# -*- coding: utf-8 -*-

# pyAggr3g470r - A Web based news aggregator.
# Copyright (C) 2010-2014  Cédric Bonhomme - http://cedricbonhomme.org/
#
# For more information : https://bitbucket.org/cedricbonhomme/pyaggr3g470r/
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

__author__ = "Cedric Bonhomme"
__version__ = "$Revision: 4.8 $"
__date__ = "$Date: 2010/01/29 $"
__revision__ = "$Date: 2014/04/12 $"
__copyright__ = "Copyright (c) Cedric Bonhomme"
__license__ = "AGPLv3"

import os
import subprocess
import datetime
from flask import render_template, request, flash, session, url_for, redirect, g, current_app, make_response
from flask.ext.login import LoginManager, login_user, logout_user, login_required, current_user, AnonymousUserMixin
from flask.ext.principal import Principal, Identity, AnonymousIdentity, identity_changed, identity_loaded, Permission, RoleNeed, UserNeed
from sqlalchemy import desc
from werkzeug import generate_password_hash

import conf
import utils
import export
import models
if not conf.ON_HEROKU:
    import search as fastsearch
from forms import SigninForm, AddFeedForm, ProfileForm
from pyaggr3g470r import app, db, allowed_file
from pyaggr3g470r.models import User, Feed, Article, Role

Principal(app)
# Create a permission with a single Need, in this case a RoleNeed.
admin_permission = Permission(RoleNeed('admin'))

login_manager = LoginManager()
login_manager.init_app(app)

#
# Management of the user's session.
#
@identity_loaded.connect_via(app)
def on_identity_loaded(sender, identity):
    # Set the identity user object
    identity.user = current_user

    # Add the UserNeed to the identity
    if hasattr(current_user, 'id'):
        identity.provides.add(UserNeed(current_user.id))

    # Assuming the User model has a list of roles, update the
    # identity with the roles that the user provides
    if hasattr(current_user, 'roles'):
        for role in current_user.roles:
            identity.provides.add(RoleNeed(role.name))

@app.before_request
def before_request():
    g.user = current_user
    if g.user.is_authenticated():
        pass
        #g.user.last_seen = datetime.utcnow()
        #db.session.add(g.user)
        #db.session.commit()

@login_manager.user_loader
def load_user(email):
    # Return an instance of the User model
    return User.query.filter(User.email == email).first()


#
# Custom error pages.
#
@app.errorhandler(401)
def authentication_required(e):
    flash('Authentication required.', 'info')
    return redirect(url_for('login'))

@app.errorhandler(403)
def authentication_failed(e):
    flash('Forbidden.', 'danger')
    return redirect(url_for('home'))

@app.errorhandler(404)
def page_not_found(e):
    return render_template('errors/404.html'), 404

@app.errorhandler(500)
def page_not_found(e):
    return render_template('errors/500.html'), 500


def redirect_url(default='home'):
    return request.args.get('next') or \
            request.referrer or \
            url_for(default)


from functools import wraps
def feed_access_required(func):
    """
    This decorator enables to check if a user has access to a feed.
    The administrator of the platform is able to access to the feeds of a normal user.
    """
    @wraps(func)
    def decorated(*args, **kwargs):
        if kwargs.get('feed_id', None) != None:
            feed = Feed.query.filter(Feed.id == kwargs.get('feed_id', None)).first()
            if (feed == None or feed.subscriber.id != g.user.id) and not g.user.is_admin():
                flash("This feed do not exist.", "danger")
                return redirect(url_for('home'))
        return func(*args, **kwargs)
    return decorated



#
# Views.
#
@app.route('/login/', methods=['GET', 'POST'])
def login():
    """
    Log in view.
    """
    g.user = AnonymousUserMixin()
    form = SigninForm()

    if form.validate_on_submit():
        user = User.query.filter(User.email == form.email.data).first()
        login_user(user)
        g.user = user
        identity_changed.send(current_app._get_current_object(), identity=Identity(user.id))
        flash("Logged in successfully.", 'success')
        return redirect(url_for('home'))
    return render_template('login.html', form=form)

@app.route('/logout/')
@login_required
def logout():
    """
    Log out view. Removes the user information from the session.
    """
    # Update last_seen field
    g.user.last_seen = datetime.datetime.utcnow()
    db.session.add(g.user)
    db.session.commit()

    # Remove the user information from the session
    logout_user()

    # Remove session keys set by Flask-Principal
    for key in ('identity.name', 'identity.auth_type'):
        session.pop(key, None)

    # Tell Flask-Principal the user is anonymous
    identity_changed.send(current_app._get_current_object(), identity=AnonymousIdentity())

    flash("Logged out successfully.", 'success')
    return redirect(url_for('login'))

@app.route('/')
@login_required
def home():
    """
    The home page lists most recent articles of all feeds.
    """
    user = User.query.filter(User.email == g.user.email).first()
    result = []
    for feed in user.feeds:
        new_feed = Feed()
        new_feed.id = feed.id
        new_feed.title = feed.title
        new_feed.enabled = feed.enabled
        new_feed.articles = Article.query.filter(Article.user_id == g.user.id, 
                                                 Article.feed_id == feed.id).order_by(desc("Article.date")).limit(9)
        result.append(new_feed)
    unread_articles = len(Article.query.filter(Article.user_id == g.user.id, Article.readed == False).all())
    return render_template('home.html', result=result, head_title=unread_articles)

@app.route('/fetch/', methods=['GET'])
@app.route('/fetch/<int:feed_id>', methods=['GET'])
@login_required
def fetch(feed_id=None):
    """
    Triggers the download of news.
    News are downloaded in a separated process, mandatory for Heroku.
    """
    cmd = ['python', conf.basedir+'/fetch.py', g.user.email, str(feed_id)]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    flash("Downloading articles...", 'success')
    return redirect(redirect_url())

@app.route('/about/', methods=['GET'])
@login_required
def about():
    """
    'About' page.
    """
    return render_template('about.html')

@app.route('/feeds/', methods=['GET'])
@login_required
def feeds():
    """
    Lists the subscribed  feeds in a table.
    """
    user = User.query.filter(User.email == g.user.email).first()
    return render_template('feeds.html', feeds=user.feeds)

@app.route('/feed/<int:feed_id>', methods=['GET'])
@login_required
@feed_access_required
def feed(feed_id=None):
    """
    Presents detailed information about a feed.
    """
    feed = Feed.query.filter(Feed.id == feed_id).first()
    word_size = 6
    articles = feed.articles.all()
    nb_articles = len(Article.query.filter(Article.user_id == g.user.id).all())
    top_words = utils.top_words(articles, n=50, size=int(word_size))
    tag_cloud = utils.tag_cloud(top_words)

    today = datetime.datetime.now()
    try:
        last_article = articles[0].date
        first_article = articles[-1].date
        delta = last_article - first_article
        average = round(float(len(articles)) / abs(delta.days), 2)
    except:
        last_article = datetime.datetime.fromtimestamp(0)
        first_article = datetime.datetime.fromtimestamp(0)
        delta = last_article - first_article
        average = 0
    elapsed = today - last_article

    return render_template('feed.html', head_title=utils.clear_string(feed.title), feed=feed, tag_cloud=tag_cloud, \
                        first_post_date=first_article, end_post_date=last_article , nb_articles=nb_articles, \
                        average=average, delta=delta, elapsed=elapsed)

@app.route('/article/<int:article_id>', methods=['GET'])
@login_required
def article(article_id=None):
    """
    Presents the content of an article.
    """
    article = Article.query.filter(Article.user_id == g.user.id, Article.id == article_id).first()
    if article != None:
        if not article.readed:
            article.readed = True
            db.session.commit()
        return render_template('article.html', head_title=utils.clear_string(article.title), article=article)
    flash("This article do not exist.", 'warning')
    return redirect(redirect_url())
    

@app.route('/mark_as_read/', methods=['GET'])
@app.route('/mark_as_read/<int:feed_id>', methods=['GET'])
@login_required
@feed_access_required
def mark_as_read(feed_id=None):
    """
    Mark all unreaded articles as read.
    """
    if feed_id != None:
        Article.query.filter(Article.user_id == g.user.id, Article.feed_id == feed_id,
                             Article.readed == False).update({"readed": True})
        flash('Articles marked as read.', 'info')
    else:
        Article.query.filter(Article.user_id == g.user.id, Article.readed == False).update({"readed": True})
        flash("All articles marked as read", 'info')
    db.session.commit()
    return redirect(redirect_url())

@app.route('/like/<int:article_id>', methods=['GET'])
@login_required
def like(article_id=None):
    """
    Mark or unmark an article as favorites.
    """
    Article.query.filter(Article.user_id == g.user.id, Article.id == article_id). \
                update({
                        "like": not Article.query.filter(Article.id == article_id).first().like
                       })
    db.session.commit()
    return redirect(redirect_url())

@app.route('/delete/<int:article_id>/', methods=['GET'])
@login_required
def delete(article_id=None):
    """
    Delete an article from the database.
    """
    article = Article.query.filter(Article.id == article_id).first()
    if article != None and article.source.subscriber.id == g.user.id:
        db.session.delete(article)
        db.session.commit()
        try:
            fastsearch.delete_article(g.user.id, article.feed_id, article.id)
        except:
            pass
        flash('Article "' + article.title + '" deleted.', 'success')
        return redirect(url_for('home'))
    else:
        flash('Impossible to delete the article.', 'danger')
        return redirect(url_for('home'))

@app.route('/articles/<feed_id>/', methods=['GET'])
@app.route('/articles/<feed_id>/<int:nb_articles>', methods=['GET'])
@login_required
@feed_access_required
def articles(feed_id=None, nb_articles=-1):
    """
    List articles of a feed.
    The administrator of the platform is able to access to this view for every users.
    """
    feed = Feed.query.filter(Feed.id == feed_id).first()
    new_feed = Feed()
    new_feed.id = feed.id
    new_feed.title = feed.title
    new_feed.site_link = feed.site_link
    if len(feed.articles.all()) <= nb_articles:
        nb_articles = -1
    if nb_articles == -1:
        nb_articles = int(1e9)
    new_feed.articles = Article.query.filter(Article.user_id == g.user.id, \
                                        Article.feed_id == feed.id).order_by(desc("Article.date")).limit(nb_articles)
    return render_template('articles.html', feed=new_feed, nb_articles=nb_articles)

@app.route('/favorites/', methods=['GET'])
@login_required
def favorites():
    """
    List favorites articles.
    """
    feeds_with_like = Feed.query.filter(Feed.user_id == g.user.id, Feed.articles.any(like=True))
    result, nb_favorites = [], 0
    for feed in feeds_with_like:
        new_feed = Feed()
        new_feed.id = feed.id
        new_feed.title = feed.title
        new_feed.articles = Article.query.filter(Article.user_id == g.user.id, Article.feed_id == feed.id, Article.like == True).all()
        length = len(new_feed.articles.all())
        if length != 0:
            result.append(new_feed)
            nb_favorites += length
    return render_template('favorites.html', feeds=result, nb_favorites=nb_favorites)

@app.route('/unread/', methods=['GET'])
@login_required
def unread():
    """
    List unread articles.
    """
    feeds_with_unread = Feed.query.filter(Feed.user_id == g.user.id, Feed.articles.any(readed=False))
    result, nb_unread = [], 0
    for feed in feeds_with_unread:
        new_feed = Feed()
        new_feed.id = feed.id
        new_feed.title = feed.title
        new_feed.articles = Article.query.filter(Article.user_id == g.user.id, Article.feed_id == feed.id, Article.readed == False).all()
        length = len(new_feed.articles.all())
        if length != 0:
            result.append(new_feed)
            nb_unread += length
    return render_template('unread.html', feeds=result, nb_unread=nb_unread)

@app.route('/inactives/', methods=['GET'])
@login_required
def inactives():
    """
    List of inactive feeds.
    """
    nb_days = int(request.args.get('nb_days', 365))
    user = User.query.filter(User.id == g.user.id).first()
    today = datetime.datetime.now()
    inactives = []
    for feed in user.feeds:
        try:
            last_post = feed.articles[0].date
        except IndexError:
            continue
        elapsed = today - last_post
        if elapsed > datetime.timedelta(days=nb_days):
            inactives.append((feed, elapsed))
    return render_template('inactives.html', inactives=inactives, nb_days=nb_days)

@app.route('/index_database/', methods=['GET'])
@login_required
def index_database():
    """
    Index all the database.
    """
    if not conf.ON_HEROKU:
        user = User.query.filter(User.id == g.user.id).first()
        try:
            fastsearch.create_index(user)
            flash('Database indexed.', 'success')
        except Exception as e:
            flash('An error occured (%s).' % e, 'danger')
        return redirect(url_for('home'))
    else:
        flash('Option not available on Heroku.', 'success')
        return redirect(url_for('home'))

@app.route('/export/', methods=['GET'])
@login_required
def export_articles():
    """
    Export all articles.
    """
    user = User.query.filter(User.id == g.user.id).first()
    try:
        archive_file, archive_file_name = export.export_html(user)
    except:
        flash("Error when exporting articles.", 'danger')
        return redirect(url_for('management'))
    response = make_response(archive_file)
    response.headers['Content-Type'] = 'application/x-compressed'
    response.headers['Content-Disposition'] = 'attachment; filename='+archive_file_name
    return response

@app.route('/export_opml/', methods=['GET'])
@login_required
def export_opml():
    """
    Export all feeds to OPML.
    """
    user = User.query.filter(User.id == g.user.id).first()
    response = make_response(render_template('opml.xml', user=user, now=datetime.datetime.now()))
    response.headers['Content-Type'] = 'application/xml'
    response.headers['Content-Disposition'] = 'attachment; filename=feeds.opml'
    return response

@app.route('/search/', methods=['GET'])
@login_required
def search():
    """
    Search articles corresponding to the query.
    """
    if conf.ON_HEROKU:
        flash("Full text search is not yet implemented for Heroku.", "warning")
        return redirect(url_for('home'))
    user = User.query.filter(User.id == g.user.id).first()

    search_result, result = [], []
    nb_articles = 0

    query = request.args.get('query', None)
    if query != None:
        try:
            search_result, nb_articles = fastsearch.search(user.id, query)
        except Exception as e:
            flash('An error occured (%s).' % e, 'danger')
        for feed_id in search_result:
            for feed in user.feeds:
                if feed.id == feed_id:
                    new_feed = Feed()
                    new_feed.id = feed.id
                    new_feed.title = feed.title
                    new_feed.articles = []
                    for article_id in search_result[feed_id]:
                        current_article = Article.query.filter(Article.user_id == g.user.id, Article.id == article_id).first()
                        new_feed.articles.append(current_article)
                    new_feed.articles = sorted(new_feed.articles, key=lambda t: t.date, reverse=True)
                    result.append(new_feed)
                    break
    return render_template('search.html', feeds=result, nb_articles=nb_articles, query=query)

@app.route('/management/', methods=['GET', 'POST'])
@login_required
def management():
    """
    Display the management page.
    """
    if request.method == 'POST':
        # Import an OPML file
        data = request.files.get('opmlfile', None)
        if None == data or not allowed_file(data.filename):
            flash('File not allowed.', 'danger')
        else:  
            opml_path = os.path.join("./pyaggr3g470r/var/", data.filename)
            data.save(opml_path)
            try:
                nb = utils.import_opml(g.user.email, opml_path)
                flash(str(nb) + " feeds imported.", "success")
            except Exception as e:
                flash("Impossible to import the new feeds.", "danger")


    form = AddFeedForm()
    user = User.query.filter(User.id == g.user.id).first()
    nb_feeds = len(user.feeds.all())
    nb_articles = len(Article.query.filter(Article.user_id == g.user.id).all())
    nb_unread_articles = len(Article.query.filter(Article.user_id == g.user.id, Article.readed == False).all())
    return render_template('management.html', form=form, \
                            nb_feeds=nb_feeds, nb_articles=nb_articles, nb_unread_articles=nb_unread_articles, \
                            not_on_heroku = not conf.ON_HEROKU)

@app.route('/history/', methods=['GET'])
@login_required
def history():
    user = User.query.filter(User.id == g.user.id).first()
    return render_template('history.html')

@app.route('/create_feed/', methods=['GET', 'POST'])
@app.route('/edit_feed/<int:feed_id>', methods=['GET', 'POST'])
@login_required
@feed_access_required
def edit_feed(feed_id=None):
    """
    Add or edit a feed.
    """
    feed = Feed.query.filter(Feed.id == feed_id).first()
    form = AddFeedForm()

    if request.method == 'POST':
        if form.validate() == False:
            return render_template('edit_feed.html', form=form)
        if feed_id != None:
            # Edit an existing feed
            form.populate_obj(feed)
            db.session.commit()
            flash('Feed "' + feed.title + '" successfully updated.', 'success')
            return redirect('/edit_feed/' + str(feed_id))
        else:
            # Create a new feed
            existing_feed = [feed for feed in g.user.feeds if feed.link == form.link.data]
            if len(existing_feed) == 0:
                new_feed = Feed(title=form.title.data, description="", link=form.link.data, \
                                site_link=form.site_link.data, email_notification=form.email_notification.data, \
                                enabled=form.enabled.data)
                g.user.feeds.append(new_feed)
                #user.feeds = sorted(user.feeds, key=lambda t: t.title.lower())
                db.session.commit()
                flash('Feed "' + new_feed.title + '" successfully created.', 'success')
                return redirect('/edit_feed/' + str(new_feed.id))
            else:
                flash('Feed "' + existing_feed[0].title + '" already in the database.', 'warning')
                return redirect('/edit_feed/' + str(existing_feed[0].id))

    if request.method == 'GET':
        if feed_id != None:
            form = AddFeedForm(obj=feed)
            return render_template('edit_feed.html', action="Edit the feed", form=form, feed=feed, \
                                    not_on_heroku = not conf.ON_HEROKU)

        # Return an empty form in order to create a new feed
        return render_template('edit_feed.html', action="Add a feed", form=form, \
                                not_on_heroku = not conf.ON_HEROKU)

@app.route('/delete_feed/<feed_id>', methods=['GET'])
@login_required
@feed_access_required
def delete_feed(feed_id=None):
    """
    Delete a feed with all associated articles.
    """
    feed = Feed.query.filter(Feed.id == feed_id).first()
    db.session.delete(feed)
    db.session.commit()
    flash('Feed "' + feed.title + '" successfully deleted.', 'success')
    return redirect(redirect_url())

@app.route('/profile/', methods=['GET', 'POST'])
@login_required
def profile():
    """
    Edit the profile of the currently logged user.
    """
    user = User.query.filter(User.email == g.user.email).first()
    form = ProfileForm()

    if request.method == 'POST':
        if form.validate():
            form.populate_obj(user)
            if form.password.data != "":
                user.set_password(form.password.data)
            db.session.commit()
            flash('User "' + user.firstname + '" successfully updated.', 'success')
            return redirect(url_for('profile'))
        else:
            return render_template('profile.html', form=form)

    if request.method == 'GET':
        form = ProfileForm(obj=user)
        return render_template('profile.html', user=user, form=form)



#
# Views dedicated to administration tasks.
#
@app.route('/admin/dashboard/', methods=['GET', 'POST'])
@login_required
@admin_permission.require(http_exception=403)
def dashboard():
    """
    Adminstrator's dashboard.
    """
    users = User.query.all()
    users.remove(g.user)
    return render_template('admin/dashboard.html', users=users)

@app.route('/admin/create_user/', methods=['GET', 'POST'])
@app.route('/admin/edit_user/<int:user_id>/', methods=['GET', 'POST'])
@login_required
@admin_permission.require(http_exception=403)
def create_user(user_id=None):
    """
    Create or edit a user.
    """
    form = ProfileForm()

    if request.method == 'POST':
        if form.validate():
            if user_id != None:
                # Edit a user
                user = User.query.filter(User.id == user_id).first()
                form.populate_obj(user)
                if form.password.data != "":
                    user.set_password(form.password.data)
                db.session.commit()
                flash('User "' + user.firstname + '" successfully updated.', 'success')
            else:
                # Create a new user
                role_user = Role.query.filter(Role.name == "user").first()
                user = User(firstname=form.firstname.data,
                             lastname=form.lastname.data,
                             email=form.email.data,
                             pwdhash=generate_password_hash(form.password.data))
                user.roles.extend([role_user])
                db.session.add(user)
                db.session.commit()
                flash('User "' + user.firstname + '" successfully created.', 'success')
            return redirect("/admin/edit_user/"+str(user.id)+"/")
        else:
            return render_template('profile.html', form=form)

    if request.method == 'GET':
        if user_id != None:
            user = User.query.filter(User.id == user_id).first()
            form = ProfileForm(obj=user)
            message = "Edit the user <i>" + user.firstname + "</i>"
        else:
            form = ProfileForm()
            message="Add a new user"
        return render_template('/admin/create_user.html', form=form, message=message)

@app.route('/admin/user/<int:user_id>/', methods=['GET'])
@login_required
@admin_permission.require(http_exception=403)
def user(user_id=None):
    """
    See information about a user (stations, etc.).
    """
    user = User.query.filter(User.id == user_id).first()
    if user is not None:
        return render_template('/admin/user.html', user=user)
    else:
        flash('This user does not exist.', 'danger')
        return redirect(redirect_url())

@app.route('/admin/delete_user/<int:user_id>/', methods=['GET'])
@login_required
@admin_permission.require(http_exception=403)
def delete_user(user_id=None):
    """
    Delete a user (with its stations and measures).
    """
    user = User.query.filter(User.id == user_id).first()
    if user is not None:
        db.session.delete(user)
        db.session.commit()
        flash('User "' + user.firstname + '" successfully deleted.', 'success')
    else:
        flash('This user does not exist.', 'danger')
    return redirect(redirect_url())

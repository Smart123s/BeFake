import json
import logging
import random
import string
from functools import wraps
from pathlib import Path

import click
from rich.logging import RichHandler

from .BeFake import BeFake
from .config import CONFIG
from .models.post import Location, Post

logging.basicConfig(
    level=logging.DEBUG,
    format="%(message)s",
    datefmt="[%X]",
    handlers=[RichHandler()],
)
logging.getLogger('urllib3').setLevel(logging.WARNING)

BASE_DIR = Path.cwd()  # TODO: make this configurable


def load_bf(func):
    """
    Loads the BeFake object and passes it as the first argument to the function.
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        bf = BeFake()
        bf.load()
        return func(bf, *args, **kwargs)

    return wrapper


@click.group()
@click.pass_context
def cli(ctx):
    # ensure that ctx.obj exists and is a dict (in case `cli()` is called
    # by means other than the `if` block below)
    ctx.ensure_object(dict)


@cli.command(help="Login to BeReal")
@click.argument("phone_number", type=str)
def login(phone_number):
    # NOTE: Other, deprecated login methods have been removed. Check the git history if you need them.
    bf = BeFake()
    bf.request_otp(phone_number)
    otp = input("Enter OTP: ")
    bf.verify_otp(otp)
    bf.save()
    click.echo("Login successful.")
    click.echo("You can now try to use the other commands ;)")


@cli.command(help="Get info about your account")
@load_bf
def me(bf):
    user = bf.get_user_info()
    click.echo(user)
    click.echo(user.__dict__)


@cli.command(help="Refresh token")
@load_bf
def refresh(bf):
    # If the token has expired, bf.refresh_tokens() will also get called by @load_bf.
    # In that scenario, a double refresh will be done.
    # Since this command is mostly used for debugging, it wouldn't be practical to add extra code to prevent this
    # behaviour.
    bf.firebase_refresh_tokens()
    click.echo(bf.token)


@cli.command(help="Download a feed")
@click.argument("feed_id", type=click.Choice(["friends", "friends-v1", "friends-of-friends", "discovery", "memories", "memories-v1"]))
@click.option("--save-location", required=True, help="Template for the paths where the posts should be downloaded")
@click.option("--realmoji-location", help="Template for the paths where the (non-instant) realmojis should be downloaded")
@click.option("--instant-realmoji-location", help="Template for the paths where the instant realmojis should be downloaded")
@load_bf
def feed(bf, feed_id, save_location, realmoji_location, instant_realmoji_location):
    date_format = 'YYYY-MM-DD_HH-mm-ss'
    logging.debug(f"base dir: {BASE_DIR.absolute()}")

    FEEDGETTER_MAP = {
        'friends-v1': bf.get_friendsv1_feed,
        'friends-of-friends': bf.get_fof_feed,
        'memories': bf.get_memories_feed,
        'memories-v1': bf.get_memoriesv1_feed,
    }
    feed = FEEDGETTER_MAP[feed_id]()

    instant_realmoji_location = instant_realmoji_location or realmoji_location

    def _save_post_common(item, _save_location: Path):
        """
        Just some generalization to avoid code duplication.
        Downloads info.json, primary, secondary, and bts
        """
        _save_location.mkdir(parents=True, exist_ok=True)

        (_save_location / "info.json").write_text(json.dumps(item.data_dict, indent=4))
        item.primary_photo.download(_save_location / "primary")
        item.secondary_photo.download(_save_location / "secondary")
        if item.bts_video.exists():
            # FIXME: bts_video successfully instantiates when there is none, but download() would fail
            item.bts_video.download(_save_location / "bts")

    def _save_realmojis(post, realmoji_location: str, instant_realmoji_location: str):
        for emoji in post.realmojis:
            # Differenciate between instant and non-instant realomji locations
            _realmoji_location = instant_realmoji_location if emoji.type == 'instant' else realmoji_location

            # Format realmoji location
            _realmoji_location = _realmoji_location.format(
                user=emoji.username, type=emoji.type,
                feed_id=feed_id, notification_id=item.notification_id,
                post_date=post_date, post_user=item.user.username,
                post_id=post.id, emoji_id=emoji.id,
                emoji_url_id=emoji.url_id, date='{date}'
            )
            # Getting the realmoji creation date sends an extra request
            # Only use that if it's actually needed
            if '{date}' in _realmoji_location:
                _realmoji_location = _realmoji_location.format(
                    date=emoji.date.format(date_format)
                )
            _realmoji_location_path = BASE_DIR / _realmoji_location

            _realmoji_location_path.parent.mkdir(parents=True, exist_ok=True)
            emoji.photo.download(_realmoji_location_path)

    for item in feed:
        if feed_id == "memories":
            logging.info(f"saving memory {item.memory_day}")
            _save_location = BASE_DIR / save_location.format(date=item.memory_day)
            _save_post_common(item, _save_location)

        elif feed_id == "memories-v1":
            logging.info(f"saving memory {item.memory_day}".ljust(50, " ") + item.id)
            _save_location = BASE_DIR / save_location.format(date=item.memory_day, post_id=item.id)
            _save_post_common(item, _save_location)

        elif feed_id == "friends-v1":
            for post in item.posts:
                logging.info(f"saving posts by {item.user.username}".ljust(50, " ") + post.id)
                post_date = post.creation_date.format(date_format)
                _save_location = BASE_DIR / save_location.format(
                    user=item.user.username, date=post_date, feed_id=feed_id,
                    post_id=post.id, notification_id=item.notification_id
                )
                _save_post_common(post, _save_location)

                _save_realmojis(post, realmoji_location, instant_realmoji_location)


@cli.command(help="Download friends information")
@click.option("--save-location", help="The directory where the data should be downloaded")
@load_bf
def parse_friends(bf, save_location):
    date_format = 'YYYY-MM-DD_HH-mm-ss'

    friends = bf.get_friends()
    if save_location is None:
        save_location = "/friends/{user}"

    for friend in friends:
        _save_location = save_location.format(user=friend.username)
        with open(f"{_save_location}/info.json", "w+") as f:
            json.dump(friend.data_dict, f, indent=4)

        if friend.profile_picture:
            creation_date = friend.profile_picture.get_date().format(date_format)
            friend.profile_picture.download(f"{_save_location}/{creation_date}_profile_picture")


@cli.command(help="Post the photos under /data/photos to your feed")
@click.option('visibility', '--visibility', "-v", type=click.Choice(['friends', 'friends-of-friends', 'public']),
              default='friends', show_default=True, help="Set post visibility")
@click.option('caption', '--caption', "-c", type=click.STRING, default='', show_default=False, help="Post caption")
@click.option('location', '--location', "-l", type=float, nargs=2, default=[None, None],
              help="Post location, in latitude, longitude format.")
@click.option('retakes', '--retakes', "-r", type=int, default=0, show_default=True, help="Retake counter")
@click.option('resize', '--no-resize', "-R", default=True, show_default=True,
              help="Do not resize image to upload spec (1500, 2000), upload as is.")
@click.argument('primary_path', required=False, type=click.STRING)
@click.argument('secondary_path', required=False, type=click.STRING)
@load_bf
def post(bf, visibility, caption, location, retakes, primary_path, secondary_path, resize):
    if location != [None, None]:
        loc = Location(location[0], location[1])
    primary_path = "data/photos/primary.jpg" if not primary_path else primary_path
    secondary_path = "data/photos/secondary.jpg" if not secondary_path else secondary_path
    with open("data/photos/primary.jpg", "rb") as f:
        primary_bytes = f.read()
    with open("data/photos/secondary.jpg", "rb") as f:
        secondary_bytes = f.read()
    r = Post.create_post(bf, primary=primary_bytes, secondary=secondary_bytes, is_late=False, visibility=visibility,
                         caption=caption, location=loc, retakes=retakes, resize=resize)
    click.echo(r)


@cli.command(help="View an invidual post")
@click.argument("feed_id", type=click.Choice(["friends", "friends-of-friends", "discovery"]))
@click.argument("post_id", type=click.STRING)
@load_bf
def get_post(bf: BeFake, feed_id, post_id):
    feed = {
        "friends": bf.get_friendsv1_feed(),
        "friends-of-friends": bf.get_fof_feed(),
        "discovery": bf.get_discovery_feed(),
    }[feed_id]()

    for post in feed:
        if post.id == post_id:
            click.echo(post.__dict__)


@cli.command(help="Upload random photos to BeReal servers")
@click.argument("filename", type=click.STRING)
@load_bf
def upload(bf, filename):
    with open(f"data/photos/{filename}", "rb") as f:
        data = f.read()
    r = bf.upload(data)
    click.echo(f"Your file is now uploaded to:\n\t{r}")


@cli.command(help="Add a comment to a post")
@click.argument("post_id", type=click.STRING)
@click.argument("content", type=click.STRING)
@load_bf
def comment(bf, post_id, content):
    r = bf.add_comment(post_id, content)
    click.echo(r)


@cli.command(help="Delete a given comment")
@click.argument("post_id", type=click.STRING)
@click.argument("comment_id", type=click.STRING)
@load_bf
def remove_comment(bf, post_id, comment_id):
    r = bf.delete_comment(post_id, comment_id)
    click.echo(r)


@cli.command(help="Pretend to screenshot a post")
@click.argument("post_id", type=click.STRING)
@load_bf
def screenshot(bf, post_id):
    r = bf.take_screenshot(post_id)
    click.echo(r)


@cli.command(help="Delete your post")
@load_bf
def delete_post(bf):
    r = bf.delete_post()
    click.echo(r)


@cli.command(help="Change the caption of your post")
@click.argument("caption", type=click.STRING)
@load_bf
def change_caption(bf, caption):
    r = bf.change_caption(caption)
    click.echo(r)


@cli.command(help="Gets information about a user profile")
@click.argument("user_id", type=click.STRING)
@load_bf
def get_user_profile(bf, user_id):
    r = bf.get_user_profile(user_id)
    click.echo(r)
    click.echo(r.__dict__)


@cli.command(help="Sends a notification to your friends, saying you're taking a bereal")
@click.argument("user_id", type=click.STRING, required=False)
@click.argument("username", type=click.STRING, required=False)
@load_bf
def send_push_notification(bf, user_id, username):
    r = bf.send_capture_in_progress_push(topic=user_id if user_id else None, username=username if username else None)
    click.echo(r)


@cli.command(help="post an instant realmoji")
@click.argument("post_id", type=click.STRING)
@click.argument("user_id", type=click.STRING, required=False)
@click.argument("filename", required=False, type=click.STRING)
@load_bf
def instant_realmoji(bf, post_id, user_id, filename):
    if not filename:
        filename = "primary.jpg"
    with open(f"data/photos/{filename}", "rb") as f:
        data = f.read()
    r = bf.post_instant_realmoji(post_id, user_id, data)
    click.echo(r)


@cli.command(help="Upload an emoji-specific realmoji")
@click.argument("type", type=click.Choice(CONFIG["bereal"]["realmoji-map"].keys()))
@click.argument("filename", required=False, type=click.STRING)
@load_bf
def upload_realmoji(bf, type, filename):
    if not filename:
        filename = f"{type}.jpg"
    with open(f"data/photos/{filename}", "rb") as f:
        data = f.read()
    r = bf.upload_realmoji(data, emoji_type=type)
    click.echo(r)


# currently broken, gives internal server error
@cli.command(help="Add realmoji to post")
@click.argument("post_id", type=click.STRING)
@click.argument("user_id", type=click.STRING, required=False)
@click.argument("type", type=click.Choice(CONFIG["bereal"]["realmoji-map"].keys()))
@load_bf
def emoji_realmoji(bf, post_id, user_id, type):
    type = str(type)
    # we don't have any method to know which realmojis (mapped to a type) the user already uploaded, we think, the client just stores the urls to uploaded realmojis and sends them...
    r2 = bf.post_realmoji(post_id, user_id, emoji_type=type)
    click.echo(r2)


@cli.command(help="Search for a given username.")
@click.argument("username", type=click.STRING)
@load_bf
def search_user(bf, username):
    r = bf.search_username(username)
    click.echo(r)


# TODO: there's probably a better way of doing this, for instance having friend-request <add|view|cancel>.
@cli.command(help="Get friend requests")
@click.argument("operation", type=click.Choice(["sent", "received"]))
@load_bf
def friend_requests(bf, operation):
    r = bf.get_friend_requests(operation)
    click.echo(r)


@cli.command(help="Send friend request")
@click.argument("user_id", type=click.STRING)
@click.option("-s", "--source", "source", type=click.Choice(["search", "contacts", "suggestion"]), default="search",
              show_default=True, help="Where you first found about the user")
@load_bf
def new_friend_request(bf, user_id, source):
    r = bf.add_friend(user_id, source)
    click.echo(r)


@cli.command(help="Cancel friend request")
@click.argument("user_id", type=click.STRING)
@load_bf
def cancel_friend_request(bf, user_id):
    r = bf.remove_friend_request(user_id)
    click.echo(r)


@cli.command(help="get settings")
@load_bf
def settings(bf):
    r = bf.get_settings()
    click.echo(r)


if __name__ == "__main__":
    cli(obj={})

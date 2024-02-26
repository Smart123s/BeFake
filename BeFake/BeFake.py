import hashlib
import json
import logging
import os
import platform
import urllib.parse
from time import time
from base64 import b64decode, b64encode
from typing import Optional
from Crypto.Hash import HMAC, SHA256
from uuid import uuid4

import httpx
import pendulum

from .config import CONFIG
from .models.memory import Memory
from .models.memory_v1 import Memory_v1
from .models.post import FOFPost, Post
from .models.post_v1 import PostsV1
from .models.realmoji_picture import RealmojiPicture
from .models.user import User


def _get_config_dir() -> str:
    # Source: Instaloader (MIT License)
    # https://github.com/instaloader/instaloader/blob/3cc29a4/instaloader/instaloader.py#L30-L39
    if platform.system() == "Windows":
        # on Windows, use %LOCALAPPDATA%\BeFake
        localappdata = os.getenv("LOCALAPPDATA")
        if localappdata is not None:
            return os.path.join(localappdata, "BeFake")
    # on Unix, use ~/.config/befake
    return os.path.join(os.getenv("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "befake")


def get_default_session_filename() -> str:
    """Returns default token filename for given phone number."""
    # Source: Instaloader (MIT License)
    # https://github.com/instaloader/instaloader/blob/3cc29a4/instaloader/instaloader.py#L42-L46

    if os.environ.get('IS_DOCKER', False):
        return '/data/session.json'

    config_dir = _get_config_dir()
    token_filename = f"session.json"
    return os.path.join(config_dir, token_filename)


class BeFake:
    def __init__(
            self,
            refresh_token: Optional[str] = None,
            proxies=None,
            disable_ssl=False,
            api_url="https://mobile.bereal.com/api",
    ) -> None:
        self.deviceId = str(uuid4())
        self.api_url = api_url
        self.client = httpx.Client(
            proxies=proxies,
            verify=not disable_ssl,
            headers=CONFIG["bereal"]["api-headers"],
            timeout=15,
        )
        self.client.headers["bereal-device-id"] = self.deviceId
        if refresh_token is not None:
            self.refresh_token = refresh_token
            self.refresh_tokens()

    def __repr__(self):
        return f"BeFake(user_id={self.user_id})"

    def get_session(self):
        session = {"access": {}, "firebase": {}}

        if hasattr(self, "refresh_token"):
            session["access"]["refresh_token"] = self.refresh_token
        if hasattr(self, "token"):
            session["access"]["token"] = self.token
        if hasattr(self, "expiration"):
            session["access"]["expires"] = self.expiration.timestamp()
        if hasattr(self, "firebase_refresh_token"):
            session["firebase"]["refresh_token"] = self.firebase_refresh_token
        if hasattr(self, "firebase_token"):
            session["firebase"]["token"] = self.firebase_token
        if hasattr(self, "firebase_expiration"):
            session["firebase"]["expires"] = self.firebase_expiration.timestamp()
        if hasattr(self, "user_id"):
            session["user_id"] = self.user_id

        return session

    def save(self, file_path: Optional[str] = None) -> None:
        session = {"access": {"refresh_token": self.refresh_token,
                              "token": self.token,
                              "expires": self.expiration.timestamp()},
                   "firebase": {"refresh_token": self.firebase_refresh_token,
                                "token": self.firebase_token,
                                "expires": self.firebase_expiration.timestamp()},
                   "user_id": self.user_id,
                   "device_id": self.deviceId}

        if file_path is None:
            file_path = get_default_session_filename()
        dirname = os.path.dirname(file_path)
        if dirname != '' and not os.path.exists(dirname):
            os.makedirs(dirname)
            os.chmod(dirname, 0o700)
        with open(file_path, "w") as f:
            os.chmod(file_path, 0o600)
            f.write(json.dumps(session, indent=4))

    def load(self, file_path: Optional[str] = None) -> None:
        if file_path is None:
            file_path = get_default_session_filename()
        with open(file_path, "r") as f:
            session = json.load(f)
            self.user_id = session["user_id"]
            self.refresh_token = session["access"]["refresh_token"]
            self.token = session["access"]["token"]
            self.expiration = pendulum.from_timestamp(session["access"]["expires"])

            self.firebase_refresh_token = session["firebase"]["refresh_token"]
            self.firebase_token = session["firebase"]["token"]
            self.firebase_expiration = pendulum.from_timestamp(session["firebase"]["expires"])

            # legacy session files don't have a device_id saved
            if "device_id" in session.keys():
                self.deviceId = session["device_id"]
                self.client.headers["bereal-device-id"] = self.deviceId

            if pendulum.now().add(minutes=3) >= self.expiration:
                logging.info("Refreshing access token…")
                self.refresh_tokens()

            if pendulum.now().add(minutes=3) >= self.firebase_expiration:
                logging.info("Refreshing firebase token…")
                self.firebase_refresh_tokens()

    def create_signature(self) -> str:
        """Creates a bereal-signature header
        Source: a now deleted gist.
        """
        secret_key = b'56037f4af22fb6960f3cd014e2ec71b3'

        d_id = self.deviceId  # Bereal-Device-Id
        tz = CONFIG["bereal"]["api-headers"]["bereal-timezone"]  # Bereal-Timezone
        ts = int(time())  # current timestamp

        message = b64encode(f"{d_id}{tz}{ts}".encode())
        hmac_digest = HMAC.new(secret_key, message, SHA256).digest()
        sign = b64encode(f"1:{ts}:".encode() + hmac_digest).decode()

        return sign

    def api_request(self, method: str, endpoint: str, **kwargs) -> dict:
        assert not endpoint.startswith("/")
        res = self.client.request(
            method,
            f"{self.api_url}/{endpoint}",
            headers={
                "authorization": f"Bearer {self.token}",
                "bereal-signature": self.create_signature()
            },
            **kwargs,
        )
        res.raise_for_status()
        # TODO: Include error message in exception
        return res.json()

    def request_otp(self, phone: str) -> None:
        self.phone = phone

        # Request 1: get receipt token
        res1 = self.client.post(
            "https://www.googleapis.com/identitytoolkit/v3/relyingparty/verifyClient",
            params={"key": CONFIG["google"]["api-key"]},
            json={"appToken": CONFIG["google"]["appToken"]},
            headers=CONFIG["firebase"]["headers"],
        )
        if not res1.is_success:
            raise Exception(res1.content)
        receipt = res1.json()["receipt"]

        # Request 2: get the session
        res2 = self.client.post(
            "https://www.googleapis.com/identitytoolkit/v3/relyingparty/sendVerificationCode",
            params={"key": CONFIG["google"]["api-key"]},
            json={"phoneNumber": phone, "iosReceipt": receipt},
            headers=CONFIG["firebase"]["headers"],
        )
        if not res2.is_success:
            raise Exception(res2.content)
        self.otp_session = res2.json()["sessionInfo"]

    def verify_otp(self, otp: str) -> None:
        if self.otp_session is None:
            raise Exception("No open OTP session.")
        # Request can only accept plain text JSON=> string
        data = {
            "code": otp,
            "sessionInfo": self.otp_session,
            "operation": "SIGN_UP_OR_IN"
        }
        res = self.client.post(
            "https://www.googleapis.com/identitytoolkit/v3/relyingparty/verifyPhoneNumber",
            params={"key": CONFIG["google"]["api-key"]},
            headers=CONFIG["firebase"]["headers"],
            json=data,
        )
        if not res.is_success:
            raise Exception(res.content)
        self.firebase_refresh_token = res.json()["refreshToken"]
        self.firebase_refresh_tokens()
        self.grant_access_token()

    def refresh_tokens(self) -> None:
        if self.refresh_token is None:
            raise Exception("No refresh token.")

        res = self.client.post(
            "https://auth.bereal.team/token",
            params={"grant_type": "refresh_token"},
            json={"grant_type": "refresh_token",
                  **CONFIG["bereal"]["auth-data"],
                  "refresh_token": self.refresh_token
                  })
        if not res.is_success:
            raise Exception(res.content)

        res = res.json()
        self.token = res["access_token"]
        self.token_info = json.loads(
            b64decode(res["access_token"].split(".")[1] + '=='))
        self.refresh_token = res["refresh_token"]
        self.expiration = pendulum.now().add(seconds=int(res["expires_in"]))
        self.save()

    def grant_access_token(self) -> None:
        res = self.client.post(
            "https://auth.bereal.team/token",
            params={"grant_type": "firebase"},
            json={
                "grant_type": "firebase",
                **CONFIG["bereal"]["auth-data"],
                "token": self.firebase_token
            },
        )
        if not res.is_success:
            raise Exception(res.content)

        res = res.json()

        self.token = res["access_token"]
        self.token_info = json.loads(b64decode(res["access_token"].split(".")[1] + '=='))
        self.refresh_token = res["refresh_token"]
        self.expiration = pendulum.now().add(seconds=int(res["expires_in"]))

    def firebase_refresh_tokens(self) -> None:
        res = self.client.post(
            "https://securetoken.googleapis.com/v1/token",
            params={"key": CONFIG["google"]["api-key"]},
            data={"grantType": "refresh_token",
                  "refreshToken": self.firebase_refresh_token
                  },
            headers=CONFIG["firebase"]["headers"],
        )
        if not res.is_success:
            raise Exception(res.content)
        res = res.json()
        self.firebase_refresh_token = res["refresh_token"]
        self.firebase_token = res["id_token"]
        self.firebase_expiration = pendulum.now().add(seconds=int(res["expires_in"]))
        self.user_id = res["user_id"]
        # self.save() #Cant save here because we dont have the user_id yet

    def get_account_info(self):
        res = self.client.post(
            "https://www.googleapis.com/identitytoolkit/v3/relyingparty/getAccountInfo",
            params={"key": CONFIG["google"]["api-key"]},
            data={"idToken": self.firebase_token},
        )
        if not res.is_success:
            raise Exception(res.content)

        self.user_id = res["users"][0]["localId"]

    def get_user_info(self) -> User:
        res = self.api_request("get", "person/me")
        return User(res, self)

    def get_user_profile(self, user_id) -> User:
        # here for example we have a firebase-instance-id-token header with the value from the next line, that we can just ignore (but maybe we need it later, there seem to be some changes to the API especially endpoints moving tho the cloudfunctions.net server)
        # cTn8odwxQo6DR0WFVnM9TJ:APA91bGV86nmQUkqnLfFv18IhpOak1x02sYMmKvpUAqhdfkT9Ofg29BXKXS2mbt9oE-LoHiiKViXw75xKFLeOxhb68wwvPCJF79z7V5GbCsIQi7XH1RSD8ItcznqM_qldSDjghf5N8Uo
        res = self.api_request("get", f"person/profiles/{user_id}")
        return User(res, self)

    def get_friendsv1_feed(self) -> list[PostsV1]:
        res = self.api_request("get", "feeds/friends-v1")
        user = []
        friends = []
        if res["userPosts"]:
            user = [PostsV1(res["userPosts"], self)]
        if res["friendsPosts"]:
            friends = [PostsV1(posts, self) for posts in res["friendsPosts"]]
        return user + friends

    def get_fof_feed(self):  # friends of friends feed
        res = self.api_request("get", "feeds/friends-of-friends")
        return [FOFPost(p, self) for p in res["data"]]

    def get_discovery_feed(self) -> list[Post]:
        res = self.api_request("get", "feeds/discovery")
        return [Post(p, self) for p in res["posts"]]

    def get_memories_feed(self) -> list[Memory]:
        res = self.api_request("get", "feeds/memories")
        return [Memory(mem, self) for mem in res["data"]]

    def get_memoriesv1_feed(self):
        res = self.api_request("get", "feeds/memories-v1")
        memories = [Memory_v1(mem, self) for mem in res["data"]]
        newMemories = []

        logging.info("Requesting all memories' posts")

        # get all posts from the memories and append to new list
        for mem in memories:
            logging.info(f"Requesting posts by {mem.memory_day}".ljust(50, " ") + mem.id)

            if mem.num_posts_for_moment != 1 and not mem.moment_Id.startswith("brm-"):
                postsRequest = self.api_request("get", f"feeds/memories-v1/{mem.moment_Id}")
                for post in postsRequest["posts"]:
                    newMemories.append(Memory(post, self))
            else:
                newMemories.append(Memory(mem.data_dict, self))

        return newMemories

    def delete_memory(self, memory_id: str):
        res = self.api_request("delete", f"memories/{memory_id}")
        return res

    def delete_post(self):
        res = self.api_request("delete", "content/posts")
        return res

    def get_memories_video(self):
        res = self.api_request("get", "memories/video")
        return res

    def delete_video_memory(self, memory_id: str):
        res = self.api_request("delete", f"memories/video/{memory_id}")
        return res

    def add_friend(self, user_id: str, source: str):
        res = self.api_request("post",
                               "relationships/friend-requests",
                               data={
                                   "userId": user_id,
                                   "source": source,
                               },
                               )
        return User(res, self)

    def get_friends(self):
        res = self.api_request("get", "relationships/friends")
        return [User(friend, self) for friend in res["data"]]

    def get_friend_suggestions(self, next=None):
        if next:
            res = self.api_request(
                "get", "relationships/suggestions", params={"page": next})
        else:
            res = self.api_request("get", "relationships/suggestions")

        return [User(suggestion, self) for suggestion in res["data"]], res["next"]

    def get_friend_requests(self, req_type: str):
        res = self.api_request(
            "get", f"relationships/friend-requests/{req_type}")
        return [User(user, self) for user in res["data"]]

    def get_sent_friend_requests(self):
        return self.get_friend_requests("sent")

    def get_received_friend_requests(self):
        return self.get_friend_requests("received")

    def remove_friend_request(self, userId):
        res = self.api_request(
            "patch", f"relationships/friend-requests/{userId}", data={"status": "cancelled"})
        return User(res, self)

    def get_users_by_phone_numbers(self, phone_numbers):
        hashed_phone_numbers = [
            hashlib.sha256(phone_number.encode("utf-8")).hexdigest()
            for phone_number in phone_numbers
        ]
        res = self.api_request(
            "post",
            "relationships/contacts",
            data={"phoneNumbers": hashed_phone_numbers},
        )
        return [User(user, self) for user in res]

    def get_user_by_phone_number(self, phone_number: str):
        return self.get_users_by_phone_numbers([phone_number])[0]

    def send_capture_in_progress_push(self, topic=None, username=None):  # Outdated?
        topic = topic if topic else self.user_id
        username = username if username else self.get_user_info().username
        res = self.client.post(
            "https://us-central1-alexisbarreyat-bereal.cloudfunctions.net/sendCaptureInProgressPush",
            headers={
                "authorization": f"Bearer {self.token}",
            },
            json={"data": {
                "photoURL": "",
                "topic": topic,
                "username": username
            }}
        ).json()
        return res

    def change_caption(self, caption: str):
        res = self.api_request(
            "patch", f"content/posts/caption", data={"caption": caption})
        return res

    def upload(self, data: bytes):  # Broken?
        file = RealmojiPicture({})
        file.upload(self, data)
        print(file.url)
        return file

    def take_screenshot(self, post_id):
        payload = {
            "postId": post_id,
        }
        res = self.client.post(f"{self.api_url}/content/screenshots", params=payload,
                               headers={"authorization": self.token})
        return res.content

    def add_comment(self, post_id, comment):
        payload = {
            "postId": post_id,
        }
        data = {
            "content": comment,
        }
        res = self.api_request("post", "content/comments",
                               params=payload, data=data)
        return res

    def delete_comment(self, post_id, comment_id):
        payload = {
            "postId": post_id,
        }
        data = {
            "commentIds": comment_id,
        }
        res = self.api_request(
            "delete", "content/comments", params=payload, data=data)
        return res

    def upload_realmoji(self, image_file: bytes, emoji_type: str):
        picture = RealmojiPicture({})
        path = picture.upload(self, image_file)
        if emoji_type not in CONFIG["bereal"]["realmoji-map"]:
            raise ValueError("Not a valid emoji type")

        data = {
            "media": {
                "bucket": "storage.bere.al",
                "path": path,
                "width": picture.width,
                "height": picture.height
            },
            "emoji": CONFIG["bereal"]["realmoji-map"][emoji_type]
        }

        res = self.api_request("put", "person/me/realmojis", data=data)
        return res

    def post_realmoji(
            self,
            post_id: str,
            user_id: str,
            emoji_type: str,
    ):
        if emoji_type not in CONFIG["bereal"]["realmoji-map"]:
            raise ValueError("Not a valid emoji type")

        payload = {
            "postId": post_id,
            "postUserId": user_id
        }

        json_data = {
            "emoji": CONFIG["bereal"]["realmoji-map"][emoji_type]
        }
        res = self.api_request("put", "content/realmojis", params=payload, json=json_data)
        return res

    def post_instant_realmoji(self, post_id: str, owner_id: str, image_file: bytes):
        picture = RealmojiPicture({})
        path = picture.upload(self, image_file)
        json_data = {
            "media": {
                "bucket": "storage.bere.al",
                "path": path,
                "width": 500,
                "height": 500
            }
        }
        payload = {
            "postId": post_id,
            "postUserId": owner_id
        }

        res = self.client.put("https://mobile.bereal.com/api/content/realmojis/instant", params=payload,
                              content=json.dumps(json_data), headers={"authorization": f"Bearer {self.token}",
                                                                      "content-type": "application/json;charset=utf-8"})
        return res.json()

    # works also for not friends and unpublic post with given post_id
    def get_reactions(self, post_id: str):
        payload = {
            "postId": post_id,
        }
        res = self.api_request("get", f"content/realmojis",
                               params=payload,
                               )
        return res

    def search_username(self, username: str):
        res = self.api_request("get", f"search/profile",
                               params={"query": username})
        return [User(user, self) for user in res["data"]]

    def get_settings(self):
        res = self.api_request("get", f"settings")
        return res

    def get_terms(self):
        res = self.api_request("get", f"terms")
        return res

    def set_terms(self, code: str, choice: bool):
        if choice:
            res = self.api_request(
                "put", f"terms/{code}", data={"status": "ACCEPTED"})
        else:
            res = self.api_request(
                "put", f"terms/{code}", data={"status": "DECLINED"})
        return res

    def set_profile_picture(self, picture: bytes):
        payload = {
            'upload-file': ('profile-picture.webp', picture, 'image/webp')}
        res = self.api_request(
            "put", f"person/me/profile-picture", files=payload)
        return res

    def remove_profile_picture(self):
        res = self.api_request("delete", "person/me/profile-picture")
        return res

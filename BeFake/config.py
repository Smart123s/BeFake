CONFIG = {
    "bereal": {
        "api-headers": {
            # █ bereal-* headers
            "bereal-app-version-code": "14549",
            "bereal-signature": "anything",
            "bereal-timezone": "Europe/London",  # UTC Timezone
            # █ other headers
            "accept": "*/*",
            "accept-language": "en",
            "Connection": "Keep-Alive",
            "User-Agent": "okhttp/4.11.0",
        },
        "auth-data": {
            "client_id": "android",
            "client_secret": "F5A71DA-32C7-425C-A3E3-375B4DACA406",
        },
        "realmoji-map": {
            "up": "👍",
            "happy": "😃",
            "surprised": "😲",
            "laughing": "😍",
            "heartEyes": "😂"
        },
    },
    "firebase": {
        "headers": {
            "x-client-version": "iOS/FirebaseSDK/9.6.0/FirebaseCore-iOS",
            "x-ios-bundle-identifier": "AlexisBarreyat.BeReal",
            "accept-language": "en",
            "user-agent": "FirebaseAuth.iOS/9.6.0 AlexisBarreyat.BeReal/0.31.0 iPhone/14.7.1 hw/iPhone9_1",
            "x-firebase-locale": "en",
        },
    },
    "google": {
        "api-key": "AIzaSyDwjfEeparokD7sXPVQli9NsTuhT6fJ6iA",
        "appToken": "54F80A258C35A916B38A3AD83CA5DDD48A44BFE2461F90831E0F97EBA4BB2EC7",
    },
}

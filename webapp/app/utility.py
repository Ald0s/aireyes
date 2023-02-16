from datetime import timedelta
from collections import OrderedDict


class TimeDeltaDescriptor():
    def display(self, **kwargs):
        self._minified_keys = kwargs.get("minified_keys", self._minified_keys)
        self._use_commas = kwargs.get("use_commas", self._use_commas)
        self._use_and = kwargs.get("use_and", self._use_and)

        # Displaying will display the first non-zero value, and its directly following item in the ordered dictionary 'd.'
        dict_items = list(self.d.items())
        for idx, dict_item in enumerate(dict_items):
            key, value = dict_item
            display_item = self._display_key_value(key, value)
            # If value is 0, continue.
            if value == 0:
                continue
            # If there is no next item, only return a display with the current.
            if idx+1 >= len(dict_items):
                return self._strfdelta(f"{display_item}")
            # Get the next.
            next_key, next_value = dict_items[idx+1]
            # If next value is 0, do not display that.
            if not next_value:
                return self._strfdelta(f"{display_item}")
            if self._use_commas:
                comma = ", "
            elif self._use_and:
                comma = " and "
            else:
                comma = " "
            next_display_item = self._display_key_value(next_key, next_value)
            return self._strfdelta(f"{display_item}{comma}{next_display_item}")
        return None

    def __init__(self, tdelta, **kwargs):
        self._minified_keys = kwargs.get("minified_keys", False)
        self._use_commas = kwargs.get("use_commas", True)
        self._use_and = kwargs.get("use_and", False)

        self._tdelta = tdelta
        self.d = OrderedDict(years = 0, months = 0, weeks = 0, days = 0, hours = 0, minutes = 0, seconds = 0)
        if self._tdelta.days > 0:
            self.d["years"], rem = divmod(self._tdelta.days, 365)
            self.d["months"], rem = divmod(rem, 30)
            self.d["weeks"], rem = divmod(rem, 7)
            self.d["days"] = rem
        # Minus all days from the timedelta to be left with just the number of seconds remaining...
        new_tdelta = self._tdelta - timedelta(days = self._tdelta.days)
        self.d["hours"], rem = divmod(new_tdelta.seconds, 3600)
        self.d["minutes"], self.d["seconds"] = divmod(rem, 60)

    def _display_key_value(self, key, value):
        # Convert key to the appropriate equivalent.
        appropriate_key = self._get_appropriate_key(key, value)
        # Now, if we're using mini keys, there is no space between the value and key.
        if self._minified_keys:
            return f"{value}{appropriate_key}"
        return f"{value} {appropriate_key}"

    def _get_appropriate_key(self, key, value):
        # If we required minified keys, we'll convert the key to its 1 letter equivalent; years -> y
        if self._minified_keys:
            return key[0].lower()
        # If value is 1, we will always scrub the 's' of the very end of key, if there is one.
        if value == 1:
            return key.strip("s")
        return key

    def _strfdelta(self, fmt):
        return fmt.format(**self.d)

"""Adds config flow for Vulcan."""
import logging

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from aiohttp import ClientConnectionError
from homeassistant import config_entries
from homeassistant.const import CONF_PIN, CONF_REGION, CONF_SCAN_INTERVAL, CONF_TOKEN
from homeassistant.core import callback
from vulcan import Account, Keystore, Vulcan
from vulcan._utils import VulcanAPIException

from . import DOMAIN
from .const import (
    CONF_ATTENDANCE_NOTIFY,
    CONF_GRADE_NOTIFY,
    CONF_LESSON_ENTITIES_NUMBER,
    CONF_MESSAGE_NOTIFY,
    DEFAULT_LESSON_ENTITIES_NUMBER,
    DEFAULT_SCAN_INTERVAL,
)
from .register import register

_LOGGER = logging.getLogger(__name__)

LOGIN_SCHEMA = {
    vol.Required(CONF_TOKEN): str,
    vol.Required(CONF_REGION): str,
    vol.Required(CONF_PIN): str,
}


class VulcanFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a Uonet+ Vulcan config flow."""

    VERSION = 2

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return VulcanOptionsFlowHandler(config_entry)

    async def async_step_user(self, user_input=None):
        """Handle config flow."""
        if self._async_current_entries():
            return await self.async_step_add_next_config_entry()

        return await self.async_step_auth()

    async def async_step_auth(self, user_input=None, errors=None):
        """Authorize integration."""

        if user_input is not None:
            try:
                credentials = await register(
                    self.hass,
                    user_input[CONF_TOKEN],
                    user_input[CONF_REGION],
                    user_input[CONF_PIN],
                )
            except VulcanAPIException as err:
                if str(err) == "Invalid token!" or str(err) == "Invalid token.":
                    errors = {"base": "invalid_token"}
                elif str(err) == "Expired token.":
                    errors = {"base": "expired_token"}
                elif str(err) == "Invalid PIN.":
                    errors = {"base": "invalid_pin"}
                else:
                    errors = {"base": "unknown"}
                    _LOGGER.error(err)
            except RuntimeError as err:
                if str(err) == "Internal Server Error (ArgumentException)":
                    errors = {"base": "invalid_symbol"}
                else:
                    errors = {"base": "unknown"}
                    _LOGGER.error(err)
            except ClientConnectionError as err:
                errors = {"base": "cannot_connect"}
                _LOGGER.error("Connection error: %s", err)
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors = {"base": "unknown"}
            if not errors:
                account = credentials["account"]
                keystore = credentials["keystore"]
                client = Vulcan(keystore, account)
                students = await client.get_students()
                await client.close()

                if len(students) > 1:
                    # pylint:disable=attribute-defined-outside-init
                    self.account = account
                    self.keystore = keystore
                    self.students = students
                    return await self.async_step_select_student()
                student = students[0]
                await self.async_set_unique_id(str(student.pupil.id))
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"{student.pupil.first_name} {student.pupil.last_name}",
                    data={
                        "student_id": str(student.pupil.id),
                        "keystore": keystore.as_dict,
                        "account": account.as_dict,
                    },
                )

        return self.async_show_form(
            step_id="auth",
            data_schema=vol.Schema(LOGIN_SCHEMA),
            errors=errors,
        )

    async def async_step_select_student(self, user_input=None):
        """Allow user to select student."""
        errors = {}
        students_list = {}
        if self.students is not None:
            for student in self.students:
                students_list[
                    str(student.pupil.id)
                ] = f"{student.pupil.first_name} {student.pupil.last_name}"
        if user_input is not None:
            student_id = user_input["student"]
            await self.async_set_unique_id(str(student_id))
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=students_list[student_id],
                data={
                    "student_id": str(student_id),
                    "keystore": self.keystore.as_dict,
                    "account": self.account.as_dict,
                },
            )

        data_schema = {
            vol.Required(
                "student",
            ): vol.In(students_list),
        }
        return self.async_show_form(
            step_id="select_student",
            data_schema=vol.Schema(data_schema),
            errors=errors,
        )

    async def async_step_select_saved_credentials(self, user_input=None, errors=None):
        """Allow user to select saved credentials."""
        credentials_list = {}
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            credentials_list[entry.entry_id] = entry.data["account"]["UserName"]

        if user_input is not None:
            entry = self.hass.config_entries.async_get_entry(user_input["credentials"])
            keystore = Keystore.load(entry.data["keystore"])
            account = Account.load(entry.data["account"])
            client = Vulcan(keystore, account)
            try:
                students = await client.get_students()
            except VulcanAPIException as err:
                if str(err) == "The certificate is not authorized.":
                    return await self.async_step_auth(
                        errors={"base": "expired_credentials"}
                    )
                _LOGGER.error(err)
                return await self.async_step_auth(errors={"base": "unknown"})
            except ClientConnectionError as err:
                _LOGGER.error("Connection error: %s", err)
                return await self.async_step_select_saved_credentials(
                    errors={"base": "cannot_connect"}
                )
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                return await self.async_step_auth(errors={"base": "unknown"})
            finally:
                await client.close()
            if len(students) == 1:
                student = students[0]
                await self.async_set_unique_id(str(student.pupil.id))
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"{student.pupil.first_name} {student.pupil.last_name}",
                    data={
                        "student_id": str(student.pupil.id),
                        "keystore": keystore.as_dict,
                        "account": account.as_dict,
                    },
                )
            # pylint:disable=attribute-defined-outside-init
            self.account = account
            self.keystore = keystore
            self.students = students
            return await self.async_step_select_student()

        data_schema = {
            vol.Required(
                "credentials",
            ): vol.In(credentials_list),
        }
        return self.async_show_form(
            step_id="select_saved_credentials",
            data_schema=vol.Schema(data_schema),
            errors=errors,
        )

    async def async_step_add_next_config_entry(self, user_input=None):
        """Flow initialized when user is adding next entry of that integration."""
        existing_entries = []
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            existing_entries.append(entry)

        errors = {}
        if user_input is not None:
            if user_input["use_saved_credentials"]:
                if len(existing_entries) == 1:
                    keystore = Keystore.load(existing_entries[0].data["keystore"])
                    account = Account.load(existing_entries[0].data["account"])
                    client = Vulcan(keystore, account)
                    students = await client.get_students()
                    await client.close()
                    new_students = []
                    existing_entry_ids = []
                    for entry in self.hass.config_entries.async_entries(DOMAIN):
                        existing_entry_ids.append(entry.data["student_id"])
                    for student in students:
                        if str(student.pupil.id) not in existing_entry_ids:
                            new_students.append(student)
                    if not new_students:
                        return self.async_abort(reason="all_student_already_configured")
                    if len(new_students) == 1:
                        await self.async_set_unique_id(str(new_students[0].pupil.id))
                        self._abort_if_unique_id_configured()
                        return self.async_create_entry(
                            title=f"{new_students[0].pupil.first_name} {new_students[0].pupil.last_name}",
                            data={
                                "student_id": str(new_students[0].pupil.id),
                                "keystore": keystore.as_dict,
                                "account": account.as_dict,
                            },
                        )
                    # pylint:disable=attribute-defined-outside-init
                    self.account = account
                    self.keystore = keystore
                    self.students = new_students
                    return await self.async_step_select_student()
                return await self.async_step_select_saved_credentials()
            return await self.async_step_auth()

        data_schema = {
            vol.Required("use_saved_credentials", default=True): bool,
        }
        return self.async_show_form(
            step_id="add_next_config_entry",
            data_schema=vol.Schema(data_schema),
            errors=errors,
        )

    async def async_step_reauth(self, user_input=None):
        """Reauthorize integration."""
        errors = {}
        if user_input is not None:
            try:
                credentials = await register(
                    self.hass,
                    user_input[CONF_TOKEN],
                    user_input[CONF_REGION],
                    user_input[CONF_PIN],
                )
            except VulcanAPIException as err:
                if str(err) == "Invalid token!" or str(err) == "Invalid token.":
                    errors["base"] = "invalid_token"
                elif str(err) == "Expired token.":
                    errors["base"] = "expired_token"
                elif str(err) == "Invalid PIN.":
                    errors["base"] = "invalid_pin"
                else:
                    errors["base"] = "unknown"
                    _LOGGER.error(err)
            except RuntimeError as err:
                if str(err) == "Internal Server Error (ArgumentException)":
                    errors["base"] = "invalid_symbol"
                else:
                    errors["base"] = "unknown"
                    _LOGGER.error(err)
            except ClientConnectionError as err:
                errors["base"] = "cannot_connect"
                _LOGGER.error("Connection error: %s", err)
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            if not errors:
                account = credentials["account"]
                keystore = credentials["keystore"]
                client = Vulcan(keystore, account)
                students = await client.get_students()
                await client.close()
                existing_entries = []
                for entry in self.hass.config_entries.async_entries(DOMAIN):
                    existing_entries.append(entry)
                for student in students:
                    for entry in existing_entries:
                        if str(student.pupil.id) == str(entry.data["student_id"]):
                            self.hass.config_entries.async_update_entry(
                                entry,
                                title=f"{student.pupil.first_name} {student.pupil.last_name}",
                                data={
                                    "student_id": str(student.pupil.id),
                                    "keystore": keystore.as_dict,
                                    "account": account.as_dict,
                                },
                            )
                            await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth",
            data_schema=vol.Schema(LOGIN_SCHEMA),
            errors=errors,
        )


class VulcanOptionsFlowHandler(config_entries.OptionsFlow):
    """Config flow options for Uonet+ Vulcan."""

    def __init__(self, config_entry):
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None):
        """Manage the options."""
        errors = {}

        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = {
            vol.Optional(
                CONF_MESSAGE_NOTIFY,
                default=self.config_entry.options.get(CONF_MESSAGE_NOTIFY, False),
            ): bool,
            vol.Optional(
                CONF_ATTENDANCE_NOTIFY,
                default=self.config_entry.options.get(CONF_ATTENDANCE_NOTIFY, False),
            ): bool,
            vol.Optional(
                CONF_GRADE_NOTIFY,
                default=self.config_entry.options.get(CONF_GRADE_NOTIFY, False),
            ): bool,
            vol.Optional(
                CONF_SCAN_INTERVAL,
                default=self.config_entry.options.get(
                    CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                ),
            ): cv.positive_int,
            vol.Optional(
                CONF_LESSON_ENTITIES_NUMBER,
                default=self.config_entry.options.get(
                    CONF_LESSON_ENTITIES_NUMBER, DEFAULT_LESSON_ENTITIES_NUMBER
                ),
            ): cv.positive_int,
        }

        return self.async_show_form(
            step_id="init", data_schema=vol.Schema(options), errors=errors
        )

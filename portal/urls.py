from django.urls import include, path

urlpatterns = [path("", include("kundenportal.urls"))]

handler403 = "kundenportal.views.fehler_403"
handler404 = "kundenportal.views.fehler_404"

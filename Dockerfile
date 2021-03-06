# Starting with the image used in helm jupyterhub
FROM jupyterhub/k8s-hub:0.11.1

USER root

COPY . /egi-notebooks-hub/

# install the hub extensions
# hadolint ignore=DL3013
RUN pip3 install --no-cache-dir /egi-notebooks-hub

# Customise Jupyter login
# TODO(enolfc): this can break quite easily, should find a better way
RUN cp /egi-notebooks-hub/templates/login.html /usr/local/share/jupyterhub/templates/login.html \
    && cp /egi-notebooks-hub/templates/401.html /usr/local/share/jupyterhub/templates/401.html \
    && cp /egi-notebooks-hub/templates/401.html /usr/local/share/jupyterhub/templates/403.html \
    && cp /egi-notebooks-hub/images/egi-notebooks.png /usr/local/share/jupyterhub/static/images/jupyter.png \
    && cp /egi-notebooks-hub/images/notebooks-logo.png /usr/local/share/jupyterhub/static/images/notebooks-logo.png \
    && cp /egi-notebooks-hub/images/cesnet.png /usr/local/share/jupyterhub/static/images/cesnet.png \
    && cp /egi-notebooks-hub/images/cesga.jpg /usr/local/share/jupyterhub/static/images/cesga.jpg \
    && cp /egi-notebooks-hub/images/infn-ct.jpg /usr/local/share/jupyterhub/static/images/infn-ct.jpg

ARG NB_USER=jovyan
USER ${NB_USER}
